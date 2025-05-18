import streamlit as st
from docx import Document
from docx.shared import Pt # Para mantener el tamaño de fuente, por ejemplo
from docx.enum.text import WD_ALIGN_PARAGRAPH # Para alineación
import language_tool_python
import re
import io
import uuid # Para generar placeholders únicos

# Configuración de la página de Streamlit
st.set_page_config(page_title="Corrector Gramatical DOCX", layout="wide")

# Inicializar LanguageTool para español
try:
    tool = language_tool_python.LanguageTool('es-ES') # o 'es' si 'es-ES' no funciona
except Exception as e:
    st.error(f"Error al inicializar LanguageTool. Asegúrate de tener Java instalado y configurado.")
    st.error(f"Detalle del error: {e}")
    tool = None # Para evitar errores posteriores si la inicialización falla

MAX_CHARS = 300000

# --- Funciones Auxiliares ---

def extract_text_and_citations(paragraph_text):
    """
    Extrae citas y las reemplaza con placeholders.
    Devuelve el texto modificado y un diccionario de placeholders a citas.
    """
    citations = {}
    # Regex mejorada para capturar varios tipos de citas:
    # 1. Entre comillas dobles o simples.
    # 2. Formato (Autor, Año) o (Autor et al., Año, p. X).
    # 3. Formato [Autor Año] o [Autor et al. Año].
    # Considera que esta regex puede necesitar ajustes para casos muy específicos.
    citation_pattern = re.compile(
        r'(".*?"|\'.*?\'|\([^)]*?\d{4}[^)]*?\)|\[[^\]]*?\d{4}[^\]]*?\])'
    )

    def replace_citation(match):
        citation_text = match.group(0)
        placeholder = f"__CITATION_{uuid.uuid4().hex}__"
        citations[placeholder] = citation_text
        return placeholder

    text_with_placeholders = citation_pattern.sub(replace_citation, paragraph_text)
    return text_with_placeholders, citations

def insert_citations_back(text_with_placeholders, citations):
    """
    Reinserta las citas originales en lugar de los placeholders.
    """
    final_text = text_with_placeholders
    for placeholder, original_citation in citations.items():
        final_text = final_text.replace(placeholder, original_citation)
    return final_text

def get_total_characters(doc):
    """Calcula el número total de caracteres en el documento."""
    count = 0
    for para in doc.paragraphs:
        count += len(para.text)
    return count

def process_document(doc_bytes):
    """
    Procesa el documento Word: corrige gramática y ortografía
    sin tocar citas y preservando formato básico.
    """
    if not tool:
        st.error("LanguageTool no está disponible. No se puede procesar el documento.")
        return None

    doc = Document(io.BytesIO(doc_bytes))
    
    total_chars = get_total_characters(doc)
    if total_chars > MAX_CHARS:
        st.error(f"El documento excede el límite de {MAX_CHARS} caracteres (tiene {total_chars}).")
        return None

    new_doc = Document() # Creamos un nuevo documento para copiar estilos y contenido

    for para_idx, para in enumerate(doc.paragraphs):
        if not para.text.strip(): # Si el párrafo está vacío o solo espacios, copiarlo tal cual
            new_para = new_doc.add_paragraph()
            # Copiar estilo de párrafo (esto es básico, estilos más complejos pueden no copiarse)
            new_para.style = para.style
            new_para.paragraph_format.alignment = para.paragraph_format.alignment
            # Aquí se podrían copiar más propiedades del paragraph_format
            continue

        original_text = para.text
        
        # 1. Extraer citas y obtener texto con placeholders
        text_with_placeholders, citations = extract_text_and_citations(original_text)
        
        # 2. Corregir el texto con placeholders
        # LanguageTool puede ser lento con textos muy largos, aunque aquí es por párrafo
        corrected_text_with_placeholders = tool.correct(text_with_placeholders)
        
        # 3. Reinsertar las citas
        final_corrected_text = insert_citations_back(corrected_text_with_placeholders, citations)

        # 4. Añadir el párrafo corregido al nuevo documento intentando preservar formato
        # Esta es la parte más delicada para preservar formato.
        # La estrategia es añadir un nuevo párrafo y luego intentar replicar los 'runs'
        # o, de forma más simple, aplicar el estilo del párrafo original y el estilo del primer 'run'.
        
        new_para = new_doc.add_paragraph()
        new_para.style = para.style # Copia el estilo del párrafo (Heading 1, Normal, etc.)
        new_para.paragraph_format.alignment = para.paragraph_format.alignment
        new_para.paragraph_format.left_indent = para.paragraph_format.left_indent
        new_para.paragraph_format.right_indent = para.paragraph_format.right_indent
        new_para.paragraph_format.first_line_indent = para.paragraph_format.first_line_indent
        new_para.paragraph_format.space_before = para.paragraph_format.space_before
        new_para.paragraph_format.space_after = para.paragraph_format.space_after
        new_para.paragraph_format.line_spacing = para.paragraph_format.line_spacing


        # Estrategia de "runs": Si el texto no cambió mucho, podríamos intentar
        # reconstruir runs, pero es complejo.
        # Una simplificación: si el párrafo original tenía runs, aplicar el formato del primer run
        # al nuevo texto. Si no, simplemente añadir el texto.

        if para.runs:
            # Tomamos el estilo del primer run como base para el nuevo párrafo.
            # Esto es una simplificación. Si un párrafo tiene múltiples estilos de run,
            # se perderán y se aplicará el del primero a todo el texto corregido.
            original_run_style = para.runs[0]
            run = new_para.add_run(final_corrected_text)
            run.bold = original_run_style.bold
            run.italic = original_run_style.italic
            run.underline = original_run_style.underline
            run.font.name = original_run_style.font.name
            if original_run_style.font.size:
                run.font.size = original_run_style.font.size
            if original_run_style.font.color and original_run_style.font.color.rgb:
                 run.font.color.rgb = original_run_style.font.color.rgb
        else:
            # Si el párrafo original no tenía runs (raro, pero posible si estaba vacío y luego se añadió texto)
            # o si preferimos no complicarnos con runs y solo añadir texto.
            new_para.add_run(final_corrected_text)
        
        # Conservar saltos de página si el párrafo original terminaba con uno
        # Esto es un poco heurístico y puede no ser perfecto
        if para._p.xpath('.//w:br[@w:type="page"]'):
            new_para.add_run().add_break(docx.enum.text.WD_BREAK.PAGE)

    # Guardar el documento procesado en un buffer de bytes
    doc_buffer = io.BytesIO()
    new_doc.save(doc_buffer)
    doc_buffer.seek(0)
    return doc_buffer

# --- Interfaz de Streamlit ---
st.title("✍️ Corrector Gramatical para Documentos Word (.docx)")
st.markdown(f"""
Sube un archivo Word (.docx) de hasta **{MAX_CHARS // 1000}k caracteres**.
La aplicación corregirá la gramática y ortografía:
- **Preservando** el formato general del documento lo mejor posible.
- **Sin modificar** las citas textuales (ej: "texto citado", (Autor, 2023), [Autor et al. 2023]).
- El resultado se podrá descargar como un nuevo archivo .docx.

**Nota sobre el formato:** La preservación del formato es compleja. Se intentará mantener estilos de párrafo,
alineación y formato básico del texto (negrita, cursiva, tamaño de fuente del primer segmento de texto de cada párrafo).
Formatos muy complejos dentro de un mismo párrafo podrían simplificarse.
""")

uploaded_file = st.file_uploader("Carga tu archivo .docx aquí", type="docx")

if uploaded_file is not None:
    file_bytes = uploaded_file.getvalue()
    
    # Mostrar nombre y tamaño del archivo
    st.info(f"Archivo cargado: {uploaded_file.name} ({len(file_bytes) / 1024:.2f} KB)")

    if st.button("🔎 Corregir Documento"):
        if not tool:
            st.error("LanguageTool no está disponible. La corrección no puede continuar.")
        else:
            with st.spinner("Procesando el documento... Esto puede tardar unos momentos."):
                try:
                    corrected_doc_buffer = process_document(file_bytes)
                    
                    if corrected_doc_buffer:
                        st.success("¡Documento procesado y corregido con éxito!")
                        
                        st.download_button(
                            label="📥 Descargar Documento Corregido (.docx)",
                            data=corrected_doc_buffer,
                            file_name=f"corregido_{uploaded_file.name}",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        )
                except Exception as e:
                    st.error(f"Ocurrió un error durante el procesamiento: {e}")
                    st.error("Detalles técnicos:")
                    st.exception(e) # Muestra el traceback completo para depuración
else:
    st.info("Esperando a que se suba un archivo .docx.")

st.markdown("---")
st.markdown("Desarrollado con ❤️ usando Streamlit, python-docx y LanguageTool.")
