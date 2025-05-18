import streamlit as st
from docx import Document
# from docx.shared import Pt # No se usa activamente en este ejemplo simplificado de formato
# from docx.enum.text import WD_ALIGN_PARAGRAPH # No se usa activamente
import google.generativeai as genai
import re
import io
import uuid # Para generar placeholders únicos
import time # Para reintentos simples

# Configuración de la página de Streamlit
st.set_page_config(page_title="Corrector Gramatical DOCX (Gemini)", layout="wide")

MAX_CHARS = 300000
GEMINI_MODEL_NAME = "gemini-1.5-flash-latest" # O gemini-pro si prefieres

# --- Funciones Auxiliares (Mismas de antes para citas y conteo) ---

def extract_text_and_citations(paragraph_text):
    citations = {}
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
    final_text = text_with_placeholders
    for placeholder, original_citation in citations.items():
        final_text = final_text.replace(placeholder, original_citation, 1) # Reemplazar solo la primera ocurrencia
    return final_text

def get_total_characters(doc):
    count = 0
    for para in doc.paragraphs:
        count += len(para.text)
    return count

# --- Funciones para Gemini ---

@st.cache_data(ttl=3600) # Cache para evitar llamadas repetidas con el mismo texto y API Key
def correct_text_with_gemini(text_to_correct, api_key, retries=3, delay=5):
    """
    Corrige el texto usando la API de Gemini.
    """
    if not text_to_correct.strip():
        return ""

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)

        prompt = f"""
        Eres un asistente experto en gramática y ortografía del idioma español.
        Tu tarea es corregir el siguiente texto.
        IMPORTANTE:
        1. Corrige únicamente errores gramaticales y ortográficos.
        2. NO cambies el significado original del texto.
        3. NO alteres ni modifiques las citas textuales que están marcadas con placeholders como __CITATION_HEXADECIMAL__. Debes dejarlas exactamente como están. Por ejemplo, si ves "__CITATION_a1b2c3d4__", esa cadena debe permanecer idéntica en tu respuesta.
        4. Devuelve SOLAMENTE el texto corregido, sin ninguna introducción, explicación, saludo, despedida o comentario adicional. No escribas "Texto corregido:" ni nada similar. Solo el texto.

        Texto a corregir:
        "{text_to_correct}"
        """
        
        generation_config = genai.types.GenerationConfig(
            # temperature=0.2, # Más bajo para ser más determinista y menos "creativo"
        )

        for attempt in range(retries):
            try:
                response = model.generate_content(prompt, generation_config=generation_config)
                # Verificar si hay texto en la respuesta
                if response.parts:
                    corrected_text = "".join(part.text for part in response.parts if hasattr(part, 'text'))
                    if corrected_text:
                         # Una limpieza adicional por si Gemini añade espacios extra alrededor de los placeholders
                        for placeholder in re.findall(r"__CITATION_[a-f0-9]{32}__", corrected_text):
                            corrected_text = corrected_text.replace(f" {placeholder} ", placeholder)
                            corrected_text = corrected_text.replace(f" {placeholder}", placeholder)
                            corrected_text = corrected_text.replace(f"{placeholder} ", placeholder)
                        return corrected_text.strip()
                
                # Si no hay texto, o la respuesta está bloqueada
                if response.prompt_feedbacks:
                    for feedback in response.prompt_feedbacks:
                        st.warning(f"Gemini API Feedback: Bloqueo - {feedback.block_reason}, Rating - {feedback.safety_ratings}")
                        if feedback.block_reason != genai.types.BlockReason.BLOCK_REASON_UNSPECIFIED: # Si hay un bloqueo real
                             return f"[BLOQUEADO POR GEMINI: {feedback.block_reason}] {text_to_correct}" # Devolver original con aviso

                st.warning(f"Intento {attempt + 1} de {retries}: Gemini devolvió una respuesta vacía o inesperada para el fragmento. Reintentando...")
                time.sleep(delay)


            except Exception as e:
                st.warning(f"Error en la API de Gemini (intento {attempt + 1}/{retries}): {e}")
                if "API key not valid" in str(e):
                    st.error("Error: La API Key de Google proporcionada no es válida. Por favor, verifica e inténtalo de nuevo.")
                    return None # Error fatal, no reintentar
                if attempt == retries - 1:
                    st.error(f"No se pudo corregir el fragmento después de {retries} intentos: {text_to_correct[:100]}...")
                    return text_to_correct # Devolver el original si todos los reintentos fallan
                time.sleep(delay * (attempt + 1)) # Backoff exponencial simple

        st.warning(f"Fragmento no procesado por Gemini después de {retries} intentos, se mantendrá original: {text_to_correct[:100]}...")
        return text_to_correct # Devolver original si todos los reintentos fallan y no hay texto

    except Exception as e:
        st.error(f"Error general al configurar o llamar a Gemini API: {e}")
        return text_to_correct # Devolver el original en caso de error de configuración

def process_document_gemini(doc_bytes, api_key):
    """
    Procesa el documento Word con Gemini: corrige gramática y ortografía
    sin tocar citas y preservando formato básico.
    """
    if not api_key:
        st.error("Por favor, introduce tu API Key de Google para continuar.")
        return None

    doc = Document(io.BytesIO(doc_bytes))
    
    total_chars = get_total_characters(doc)
    if total_chars > MAX_CHARS:
        st.error(f"El documento excede el límite de {MAX_CHARS} caracteres (tiene {total_chars}).")
        return None

    new_doc = Document()
    # Copiar estilos del documento original al nuevo (básico, para que 'Normal', 'Heading 1' etc. existan)
    # Esto no es una copia profunda de todos los estilos, pero ayuda con los básicos.
    for style in doc.styles:
        try:
            target_style = new_doc.styles.add_style(style.name, style.type)
            # Aquí se podrían copiar más atributos del estilo si fuera necesario y python-docx lo permitiera fácilmente.
        except ValueError: # El estilo ya existe (ej. 'Normal' ya está por defecto)
            pass


    progress_bar = st.progress(0)
    total_paragraphs = len(doc.paragraphs)
    processed_paragraphs = 0

    for para_idx, para in enumerate(doc.paragraphs):
        if not para.text.strip():
            new_para = new_doc.add_paragraph()
            if para.style and para.style.name in new_doc.styles:
                 new_para.style = new_doc.styles[para.style.name]
            new_para.paragraph_format.alignment = para.paragraph_format.alignment
            # Copiar más propiedades de paragraph_format aquí si es necesario
            processed_paragraphs += 1
            progress_bar.progress(processed_paragraphs / total_paragraphs)
            continue

        original_text = para.text
        
        text_with_placeholders, citations = extract_text_and_citations(original_text)
        
        if text_with_placeholders.strip(): # Solo corregir si hay texto no-cita
            corrected_text_with_placeholders = correct_text_with_gemini(text_with_placeholders, api_key)
            if corrected_text_with_placeholders is None: # Error fatal con API Key
                return None 
        else: # Si el texto original solo eran citas, o quedó vacío después de extraer citas
            corrected_text_with_placeholders = text_with_placeholders

        final_corrected_text = insert_citations_back(corrected_text_with_placeholders, citations)

        new_para = new_doc.add_paragraph()
        if para.style and para.style.name in new_doc.styles: # Aplicar estilo de párrafo si existe en el nuevo doc
            new_para.style = new_doc.styles[para.style.name]
        else: # Si no, aplicar estilo por defecto (usualmente 'Normal')
            new_para.style = new_doc.styles['Normal']


        new_para.paragraph_format.alignment = para.paragraph_format.alignment
        new_para.paragraph_format.left_indent = para.paragraph_format.left_indent
        new_para.paragraph_format.right_indent = para.paragraph_format.right_indent
        new_para.paragraph_format.first_line_indent = para.paragraph_format.first_line_indent
        new_para.paragraph_format.space_before = para.paragraph_format.space_before
        new_para.paragraph_format.space_after = para.paragraph_format.space_after
        new_para.paragraph_format.line_spacing = para.paragraph_format.line_spacing

        # Preservación básica de formato de run
        if para.runs:
            # Aplicar formato del primer run al texto completo.
            # Esto es una simplificación. Si el párrafo tiene múltiples formatos de run, se perderán.
            # Para una preservación más fiel, se necesitaría reconstruir los runs
            # y aplicar correcciones por segmentos, lo cual es mucho más complejo.
            first_run_style = para.runs[0]
            run = new_para.add_run(final_corrected_text)
            run.bold = first_run_style.bold
            run.italic = first_run_style.italic
            run.underline = first_run_style.underline
            if first_run_style.font.name:
                 run.font.name = first_run_style.font.name
            if first_run_style.font.size:
                run.font.size = first_run_style.font.size
            if first_run_style.font.color and first_run_style.font.color.rgb:
                 run.font.color.rgb = first_run_style.font.color.rgb
        else:
            new_para.add_run(final_corrected_text)
        
        # Conservar saltos de página
        # if para._p.xpath('.//w:br[@w:type="page"]'):
        #    new_para.add_run().add_break(docx.enum.text.WD_BREAK.PAGE) # WD_BREAK requiere importarlo desde docx.enum.text

        processed_paragraphs += 1
        progress_bar.progress(processed_paragraphs / total_paragraphs)
        # Pequeña pausa para no saturar la API muy rápido, especialmente si son muchos párrafos cortos
        time.sleep(0.1) 


    doc_buffer = io.BytesIO()
    new_doc.save(doc_buffer)
    doc_buffer.seek(0)
    return doc_buffer

# --- Interfaz de Streamlit ---
st.title(f"✍️ Corrector Gramatical DOCX (con Google Gemini {GEMINI_MODEL_NAME})")

st.markdown(f"""
Sube un archivo Word (.docx) de hasta **{MAX_CHARS // 1000}k caracteres**.
La aplicación corregirá la gramática y ortografía usando la API de Google Gemini:
- **Preservando** el formato general del documento lo mejor posible.
- **Sin modificar** las citas textuales (ej: "texto citado", (Autor, 2023)).
- El resultado se podrá descargar como un nuevo archivo .docx.

**Importante:** Necesitarás una API Key de Google AI Studio.
Obtén tu API key en [Google AI Studio](https://aistudio.google.com/app/apikey).
""")

# API Key Input
st.sidebar.header("Configuración")
google_api_key = st.sidebar.text_input("Ingresa tu Google API Key", type="password", help="Tu API key no será almacenada permanentemente.")

if not google_api_key:
    st.sidebar.warning("Por favor, ingresa tu Google API Key para habilitar la corrección.")
    # Intentar cargar desde st.secrets si está disponible (para despliegue)
    try:
        google_api_key_secret = st.secrets.get("GOOGLE_API_KEY")
        if google_api_key_secret:
            google_api_key = google_api_key_secret
            st.sidebar.success("API Key cargada desde los secretos de la aplicación.")
    except (FileNotFoundError, AttributeError): # AttributeError si st.secrets no existe (ej. ejecución local sin secrets.toml)
        pass


uploaded_file = st.file_uploader("Carga tu archivo .docx aquí", type="docx")

if uploaded_file is not None:
    file_bytes = uploaded_file.getvalue()
    st.info(f"Archivo cargado: {uploaded_file.name} ({len(file_bytes) / 1024:.2f} KB)")

    if st.button("🔎 Corregir Documento con Gemini"):
        if not google_api_key:
            st.error("Error: Falta la API Key de Google. Por favor, ingrésala en la barra lateral.")
        else:
            with st.spinner(f"Procesando con Gemini {GEMINI_MODEL_NAME}... Esto puede tardar (especialmente documentos largos)."):
                try:
                    corrected_doc_buffer = process_document_gemini(file_bytes, google_api_key)
                    
                    if corrected_doc_buffer:
                        st.success("¡Documento procesado y corregido con éxito!")
                        st.download_button(
                            label="📥 Descargar Documento Corregido (.docx)",
                            data=corrected_doc_buffer,
                            file_name=f"corregido_gemini_{uploaded_file.name}",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        )
                except Exception as e:
                    st.error(f"Ocurrió un error durante el procesamiento con Gemini: {e}")
                    st.exception(e)
else:
    st.info("Esperando a que se suba un archivo .docx.")

st.markdown("---")
st.markdown("Desarrollado con Streamlit, python-docx y Google Gemini.")
