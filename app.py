import streamlit as st
from docx import Document
import google.generativeai as genai
import re
import io
import uuid
import time

# Configuración de la página de Streamlit
st.set_page_config(page_title="Corrector Gramatical DOCX (Gemini)", layout="wide")

MAX_CHARS = 300000
GEMINI_MODEL_NAME = "gemini-1.5-flash-latest"

# --- Funciones Auxiliares (extract_text_and_citations, insert_citations_back, get_total_characters) ---
# (Estas funciones permanecen igual que en la versión anterior con Gemini)
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
        final_text = final_text.replace(placeholder, original_citation, 1)
    return final_text

def get_total_characters(doc):
    count = 0
    for para in doc.paragraphs:
        count += len(para.text)
    return count

# --- Funciones para Gemini ---
@st.cache_data(ttl=3600)
def correct_text_with_gemini(text_to_correct, api_key_to_use, retries=3, delay=5):
    if not text_to_correct.strip():
        return ""
    if not api_key_to_use: # Añadido chequeo aquí también
        st.error("API Key no disponible para la corrección.")
        return text_to_correct # Devolver original si no hay API key

    try:
        genai.configure(api_key=api_key_to_use) # Usar la api_key_to_use
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
        generation_config = genai.types.GenerationConfig()

        for attempt in range(retries):
            try:
                response = model.generate_content(prompt, generation_config=generation_config)
                if response.parts:
                    corrected_text = "".join(part.text for part in response.parts if hasattr(part, 'text'))
                    if corrected_text:
                        for placeholder in re.findall(r"__CITATION_[a-f0-9]{32}__", corrected_text):
                            corrected_text = corrected_text.replace(f" {placeholder} ", placeholder)
                            corrected_text = corrected_text.replace(f" {placeholder}", placeholder)
                            corrected_text = corrected_text.replace(f"{placeholder} ", placeholder)
                        return corrected_text.strip()
                
                if response.prompt_feedbacks:
                    for feedback in response.prompt_feedbacks:
                        st.warning(f"Gemini API Feedback: Bloqueo - {feedback.block_reason}, Rating - {feedback.safety_ratings}")
                        if feedback.block_reason != genai.types.BlockReason.BLOCK_REASON_UNSPECIFIED:
                            return f"[BLOQUEADO POR GEMINI: {feedback.block_reason}] {text_to_correct}"

                st.warning(f"Intento {attempt + 1} de {retries}: Gemini devolvió una respuesta vacía o inesperada. Reintentando...")
                time.sleep(delay)
            except Exception as e:
                st.warning(f"Error en la API de Gemini (intento {attempt + 1}/{retries}): {e}")
                if "API key not valid" in str(e):
                    st.error("Error: La API Key de Google proporcionada no es válida. Por favor, verifica e inténtalo de nuevo.")
                    return None # Error fatal
                if attempt == retries - 1:
                    st.error(f"No se pudo corregir el fragmento después de {retries} intentos: {text_to_correct[:100]}...")
                    return text_to_correct
                time.sleep(delay * (attempt + 1))
        return text_to_correct
    except Exception as e:
        st.error(f"Error general al configurar o llamar a Gemini API: {e}")
        return text_to_correct

def process_document_gemini(doc_bytes, api_key_to_use): # Cambiado el nombre del parámetro
    if not api_key_to_use: # Chequeo inicial
        st.error("Por favor, configura tu API Key de Google para continuar.")
        return None

    doc = Document(io.BytesIO(doc_bytes))
    total_chars = get_total_characters(doc)
    if total_chars > MAX_CHARS:
        st.error(f"El documento excede el límite de {MAX_CHARS} caracteres (tiene {total_chars}).")
        return None

    new_doc = Document()
    for style in doc.styles:
        try:
            new_doc.styles.add_style(style.name, style.type)
        except ValueError:
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
            processed_paragraphs += 1
            progress_bar.progress(processed_paragraphs / total_paragraphs)
            continue

        original_text = para.text
        text_with_placeholders, citations = extract_text_and_citations(original_text)
        
        if text_with_placeholders.strip():
            corrected_text_with_placeholders = correct_text_with_gemini(text_with_placeholders, api_key_to_use)
            if corrected_text_with_placeholders is None:
                return None 
        else:
            corrected_text_with_placeholders = text_with_placeholders

        final_corrected_text = insert_citations_back(corrected_text_with_placeholders, citations)
        new_para = new_doc.add_paragraph()

        if para.style and para.style.name in new_doc.styles:
            new_para.style = new_doc.styles[para.style.name]
        else:
            new_para.style = new_doc.styles['Normal']

        new_para.paragraph_format.alignment = para.paragraph_format.alignment
        new_para.paragraph_format.left_indent = para.paragraph_format.left_indent
        new_para.paragraph_format.right_indent = para.paragraph_format.right_indent
        new_para.paragraph_format.first_line_indent = para.paragraph_format.first_line_indent
        new_para.paragraph_format.space_before = para.paragraph_format.space_before
        new_para.paragraph_format.space_after = para.paragraph_format.space_after
        new_para.paragraph_format.line_spacing = para.paragraph_format.line_spacing

        if para.runs:
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

        processed_paragraphs += 1
        progress_bar.progress(processed_paragraphs / total_paragraphs)
        time.sleep(0.05) # Reducido un poco, ajustar según necesidad

    doc_buffer = io.BytesIO()
    new_doc.save(doc_buffer)
    doc_buffer.seek(0)
    return doc_buffer

# --- Interfaz de Streamlit ---
st.title(f"✍️ Corrector Gramatical DOCX (con Google Gemini {GEMINI_MODEL_NAME})")
st.markdown(f"""
Sube un archivo Word (.docx) de hasta **{MAX_CHARS // 1000}k caracteres**.
La aplicación corregirá la gramática y ortografía usando la API de Google Gemini.
""")

st.sidebar.header("Configuración de API Key")
# Intentar cargar la API key desde st.secrets
api_key_from_secrets = ""
try:
    # st.secrets es un diccionario o un objeto similar
    if "GOOGLE_API_KEY" in st.secrets and st.secrets["GOOGLE_API_KEY"]:
        api_key_from_secrets = st.secrets["GOOGLE_API_KEY"]
        st.sidebar.success("API Key cargada desde los secretos de la aplicación.")
    else:
        st.sidebar.info("API Key no encontrada en `secrets.toml` o está vacía.")
except (FileNotFoundError, AttributeError): # AttributeError si st.secrets no existe o no es subscriptable
    st.sidebar.info("Archivo `secrets.toml` no encontrado. Ingresa la API Key manualmente.")
    # No hacer nada más, api_key_from_secrets permanecerá vacío

# Permitir al usuario ingresar o sobrescribir la API key
# El valor por defecto será la clave de los secretos si se encontró, o vacío.
user_provided_api_key = st.sidebar.text_input(
    "Ingresa tu Google API Key (opcional si está en secrets.toml)",
    type="password",
    value=api_key_from_secrets, # Pre-rellena si se cargó de secretos
    help="Obtén tu API key en Google AI Studio. Si está en secrets.toml, se usará esa por defecto."
)

# Determinar qué API key usar: la ingresada por el usuario tiene precedencia si es diferente de la de secretos
# o si la de secretos estaba vacía y el usuario ingresó una.
final_api_key_to_use = user_provided_api_key
if not user_provided_api_key and api_key_from_secrets: # Si el input está vacío pero secretos tenía una
    final_api_key_to_use = api_key_from_secrets


uploaded_file = st.file_uploader("Carga tu archivo .docx aquí", type="docx")

if uploaded_file is not None:
    file_bytes = uploaded_file.getvalue()
    st.info(f"Archivo cargado: {uploaded_file.name} ({len(file_bytes) / 1024:.2f} KB)")

    if st.button("🔎 Corregir Documento con Gemini"):
        if not final_api_key_to_use: # Usar la clave final determinada
            st.error("Error: Falta la API Key de Google. Por favor, ingrésala o configúrala en `secrets.toml`.")
        else:
            with st.spinner(f"Procesando con Gemini {GEMINI_MODEL_NAME}... Esto puede tardar."):
                try:
                    # Pasar la clave final a la función de procesamiento
                    corrected_doc_buffer = process_document_gemini(file_bytes, final_api_key_to_use)
                    
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
