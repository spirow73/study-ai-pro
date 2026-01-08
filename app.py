import streamlit as st
from google import genai
from google.genai import types
from supabase import create_client, Client
import json
import uuid
import tempfile
from datetime import datetime
import os
import warnings
import time

# Silenciar warnings de supabase storage
warnings.filterwarnings("ignore", message=".*trailing slash.*")

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Study AI Pro", layout="wide")

# Inicializar conexión a Supabase
try:
    url = st.secrets["SUPABASE_URL"]
    # Fix: Ensure trailing slash for storage endpoint compatibility
    if not url.endswith("/"):
        url += "/"
    key = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(url, key)
except Exception as e:
    try:
        import toml
        secrets = toml.load(".streamlit/secrets.toml")
        url = secrets["SUPABASE_URL"]
        if not url.endswith("/"):
            url += "/"
        key = secrets["SUPABASE_KEY"]
        supabase: Client = create_client(url, key)
    except:
        st.warning(f"Configura los secretos de Supabase (.streamlit/secrets.toml) para persistencia. Error: {e}")
        supabase = None

# Obtener API Key de Gemini desde secrets
try:
    GEMINI_API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    try:
        import toml
        secrets = toml.load(".streamlit/secrets.toml")
        GEMINI_API_KEY = secrets["GOOGLE_API_KEY"]
    except:
        st.error("Configura GOOGLE_API_KEY en .streamlit/secrets.toml")
        GEMINI_API_KEY = None

# --- FUNCIONES DE BACKEND ---

def upload_file_to_storage(file_content, file_name, file_type, username):
    """Sube un archivo a Supabase Storage y retorna la URL"""
    if not supabase:
        return None
    
    try:
        # Generar nombre único
        file_ext = file_name.split('.')[-1]
        unique_name = f"{username}/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.{file_ext}"
        
        # Subir a Supabase Storage (bucket: 'documents')
        response = supabase.storage.from_('documents').upload(
            path=unique_name,
            file=file_content,
            file_options={"content-type": file_type}
        )
        return supabase.storage.from_('documents').get_public_url(unique_name)
    except Exception as e:
        st.warning(f"No se pudo subir a Supabase: {e}")
        return None

def generate_content_from_files(uploaded_files):
    """Sube archivos a Gemini API y genera contenido"""
    if not GEMINI_API_KEY:
        st.error("No hay API Key de Gemini configurada")
        return []

    client = genai.Client(api_key=GEMINI_API_KEY)
    gemini_files = []

    # 1. Subir archivos a Google File API
    try:
        for up_file in uploaded_files:
            # Crear archivo temporal porque la API file.upload necesita path local
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{up_file.name.split('.')[-1]}") as tmp:
                tmp.write(up_file.getvalue())
                tmp_path = tmp.name
            
            # Subir a Gemini
            g_file = client.files.upload(file=tmp_path)
            gemini_files.append(g_file)
            
            # Limpiar archivo temporal
            os.remove(tmp_path)

    except Exception as e:
        st.error(f"Error subiendo archivos a Google AI: {e}")
        return []

    # 2. Prompt
    prompt = """
    Eres un profesor experto creando material de examen de alta calidad.
    Analiza los documentos adjuntos (pueden ser PDFs, PPTs, textos) y genera una lista JSON estructurada.
    
    Identifica los conceptos clave y crea:
    - "type": "flashcard" (conceptos clave), "quiz" (test de 4 opciones) o "essay" (pregunta de desarrollo).
    - "question": la pregunta clara y precisa.
    - "answer": la respuesta correcta completa.
    - "options": (SOLO para type="quiz") un array de 4 strings.
    
    Genera una buena cantidad de preguntas variadas con un nivel de complejidad alto (mínimo 5 flashcards, 5 quiz, 3 essay).
    
    Formato JSON esperado:
    [
      {"type": "flashcard", "question": "...", "answer": "..."},
      {"type": "quiz", "question": "...", "options": ["A", "B", "C", "D"], "answer": "Opción Correcta"},
      {"type": "essay", "question": "...", "answer": "Explicación detallada..."}
    ]
    """
    
    # Lista de modelos a probar en orden de preferencia (verificados disponibles)
    models_to_try = ['gemini-2.5-flash-lite', 'gemini-2.0-flash-lite', 'gemini-2.0-flash']
    
    import time
    
    for model_name in models_to_try:
        try:
            # Intentar generar con el modelo actual
            response = client.models.generate_content(
                model=model_name,
                contents=[prompt, *gemini_files],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text)
            
        except Exception as e:
            error_msg = str(e)
            
            # Si es por límite de cuota (429), esperar un poco y reintentar con el mismo modelo o seguir
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                st.warning(f"Límite de cuota excedido en {model_name}. Esperando 5s...")
                time.sleep(5)
                try:
                    # Un reintento simple
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[prompt, *gemini_files],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json"
                        )
                    )
                    return json.loads(response.text)
                except:
                    pass # Seguir al siguiente modelo
            
            # Si no se encuentra el modelo (404), continuar
            st.warning(f"Fallo con {model_name}: {e}. Probando siguiente...")
            continue
            
    st.error("No se pudo generar contenido con ninguno de los modelos probados.")
    return []

def save_to_db(questions, topic="General"):
    """Guarda las preguntas generadas en la base de datos"""
    if not supabase: return
    data = []
    for q in questions:
        data.append({
            "topic": topic,
            "type": q["type"],
            "question": q["question"],
            "answer": q["answer"],
            "options": q.get("options")
        })
    supabase.table("questions").insert(data).execute()

def save_progress(user, q_id, is_correct, user_ans):
    if not supabase: return
    supabase.table("user_progress").insert({
        "username": user,
        "question_id": q_id,
        "is_correct": is_correct,
        "user_answer": user_ans
    }).execute()

def grade_essay(question, user_answer, correct_context):
    if not GEMINI_API_KEY:
        return {"correct": False, "feedback": "No API Key"}
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"Evalúa respuesta. Pregunta: {question}. Contexto: {correct_context}. Respuesta: {user_answer}. JSON: {{'correct': bool, 'feedback': str}}"
    
    try:
        res = client.models.generate_content(
            model='gemini-1.5-flash', contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return json.loads(res.text)
    except:
        return {"correct": False, "feedback": "Error calificando."}

# --- INTERFAZ DE USUARIO ---

if "user" not in st.session_state:
    st.session_state.user = None

if not st.session_state.user:
    st.title("🎓 Study AI Pro - Acceso")
    user_input = st.text_input("Usuario:")
    if st.button("Entrar") and user_input:
        st.session_state.user = user_input
        st.rerun()
    st.stop()

st.sidebar.title(f"👤 {st.session_state.user}")
mode = st.sidebar.radio("Navegación", ["Generar Material", "Estudiar", "Estadísticas", "Gestionar Contenido"])

if mode == "Generar Material":
    st.header("📤 Sube tus Apuntes (PDF, PPTX)")
    files = st.file_uploader("Arrastra archivos aquí", accept_multiple_files=True, type=['pptx', 'pdf'])
    topic = st.text_input("🎯 Tema/Asignatura (obligatorio)", placeholder="Ej: Matemáticas, Historia...")
    
    if st.button("✨ Analizar y Generar", type="primary"):
        if not files:
            st.warning("Por favor, sube al menos un archivo.")
        elif not topic or not topic.strip():
            st.error("⚠️ Debes escribir un nombre de tema para organizar las preguntas.")
        else:
            with st.spinner("Subiendo y analizando documentos..."):
                # 1. Guardar copia en Supabase
                for f in files:
                    upload_file_to_storage(f.getvalue(), f.name, f.type, st.session_state.user)
                
                # 2. Generar con Gemini (Files API)
                questions = generate_content_from_files(files)
                
                if questions:
                    save_to_db(questions, topic.strip())
                    st.success(f"¡Éxito! Se crearon {len(questions)} preguntas en el tema '{topic.strip()}'.")
                    with st.expander("Ver previo"):
                        st.write(questions)
                else:
                    st.error("No se pudieron generar preguntas.")

elif mode == "Estudiar":
    st.header("📖 Zona de Estudio")
    
    if not supabase:
        st.error("No hay conexión a la base de datos.")
        st.stop()
    
    # --- Obtener temas disponibles ---
    all_questions_res = supabase.table("questions").select("*").execute()
    all_questions = all_questions_res.data
    
    if not all_questions:
        st.warning("No hay preguntas guardadas. Ve a 'Generar Material' primero.")
        st.stop()
    
    # Extraer temas únicos
    topics = list(set([q.get('topic', 'General') for q in all_questions]))
    topics.insert(0, "Todos los temas")
    
    # --- Configuración de estudio ---
    col_config1, col_config2 = st.columns(2)
    
    with col_config1:
        st.markdown("##### 📖 Tema")
        selected_topic = st.selectbox("Tema:", topics, label_visibility="collapsed")
    
    with col_config2:
        st.markdown("##### 🎯 Tipo de pregunta")
        question_type = st.selectbox(
            "Tipo:",
            ["📚 Todas", "🃏 Flashcards", "✅ Quiz", "✍️ Desarrollo"],
            label_visibility="collapsed"
        )
    
    # Mapeo de tipos
    type_map = {
        "📚 Todas": None,
        "🃏 Flashcards": "flashcard",
        "✅ Quiz": "quiz",
        "✍️ Desarrollo": "essay"
    }
    selected_type = type_map[question_type]
    
    # --- Modo de repaso y botón de generar ---
    only_failed = st.toggle("❌ Solo preguntas falladas", help="Muestra solo las que has fallado anteriormente")
    
    if selected_topic != "Todos los temas":
        st.write("") # Espacio
    if selected_topic != "Todos los temas":
        st.write("") # Espacio
        
        @st.dialog("✨ Generar Preguntas con IA")
        def generate_dialog(topic_name):
            st.write(f"Configura qué tipo de preguntas quieres para **{topic_name}**.")
            
            col_n1, col_n2, col_n3 = st.columns(3)
            num_flash = col_n1.number_input("🃏 Flashcards", min_value=0, max_value=20, value=3)
            num_quiz = col_n2.number_input("✅ Quiz", min_value=0, max_value=20, value=2)
            num_essay = col_n3.number_input("✍️ Desarrollo", min_value=0, max_value=20, value=0)
            
            total = num_flash + num_quiz + num_essay
            
            st.info(f"Total a generar: **{total}** preguntas")
            
            if st.button("🚀 Generar", type="primary", use_container_width=True, disabled=(total == 0)):
                # Lógica de generación dentro del diálogo
                with st.spinner(f"🧠 Creando {total} preguntas nuevas..."):
                    existing_qs = [q['question'] for q in all_questions if q.get('topic') == selected_topic][:5]
                    
                    if GEMINI_API_KEY:
                        client = genai.Client(api_key=GEMINI_API_KEY)
                        
                        # Prompt específico solicitando cantidades exactas
                        prompt = f"""
                        Tema: {selected_topic}
                        Preguntas existentes (contexto): {chr(10).join(existing_qs)}
                        
                        Genera EXACTAMENTE {total} preguntas NUEVAS y DIFERENTES distribuidas así:
                        - {num_flash} tipo 'flashcard' (pregunta/respuesta breve)
                        - {num_quiz} tipo 'quiz' (pregunta tipo test con 4 opciones y respuesta correcta)
                        - {num_essay} tipo 'essay' (pregunta de desarrollo/abierta)
                        
                        Formato JSON estricto:
                        [
                          {{"type": "flashcard", "question": "...", "answer": "..."}},
                          {{"type": "quiz", "question": "...", "options": ["A", "B", "C", "D"], "answer": "Opción Correcta"}},
                          {{"type": "essay", "question": "...", "answer": "Explicación esperada..."}}
                        ]
                        """
                        
                        # Lógica de reintento (modelos)
                        models_to_try = ['gemini-2.0-flash-lite', 'gemini-2.5-flash', 'gemini-flash-latest']
                        new_questions = []
                        success_model = None
                        
                        for model_name in models_to_try:
                            try:
                                response = client.models.generate_content(
                                    model=model_name,
                                    contents=prompt,
                                    config=types.GenerateContentConfig(response_mime_type="application/json")
                                )
                                new_questions = json.loads(response.text)
                                success_model = model_name
                                break 
                            except Exception as e:
                                error_msg = str(e)
                                if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                                    st.toast(f"⏳ Esperando cuota ({model_name})...", icon="⚠️")
                                    time.sleep(4)
                                    try:
                                        response = client.models.generate_content(
                                            model=model_name,
                                            contents=prompt,
                                            config=types.GenerateContentConfig(response_mime_type="application/json")
                                        )
                                        new_questions = json.loads(response.text)
                                        success_model = model_name
                                        break
                                    except:
                                        pass
                                continue

                        if new_questions:
                            save_to_db(new_questions, selected_topic)
                            st.success(f"¡Listo! Se crearon {len(new_questions)} preguntas.")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("Error al generar. Inténtalo de nuevo.")

        if st.button(f"✨ Generar más preguntas con IA", type="primary", use_container_width=True):
            generate_dialog(selected_topic)
    
    st.markdown("---")
    
    # Filtrar preguntas por tema
    if selected_topic == "Todos los temas":
        db_questions = all_questions
    else:
        db_questions = [q for q in all_questions if q.get('topic') == selected_topic]
    
    # Filtrar por tipo
    if selected_type:
        db_questions = [q for q in db_questions if q.get('type') == selected_type]
        
    # --- Filtrar por falladas si se activa el modo repaso ---
    if only_failed:
        progress_res = supabase.table("user_progress").select("question_id, is_correct").eq("username", st.session_state.user).execute()
        failed_ids = set([p['question_id'] for p in progress_res.data if not p['is_correct']])
        success_ids = set([p['question_id'] for p in progress_res.data if p['is_correct']])
        review_ids = failed_ids - success_ids
        
        if not review_ids:
            review_ids = failed_ids
            
        db_questions = [q for q in db_questions if q['id'] in review_ids]
    
    if not db_questions:
        if only_failed:
            st.success("🎉 ¡Genial! No tienes preguntas falladas pendientes.")
        else:
            st.info(f"No hay preguntas con estos filtros.")
        st.stop()
    
    st.caption(f"📊 {len(db_questions)} preguntas disponibles")
    
    # --- Inicializar índice desde query params o session state ---
    # Usar query_params para persistencia en URL
    params = st.query_params
    
    if "q_index" not in st.session_state:
        # Intentar recuperar de URL
        url_index = params.get("q", "0")
        st.session_state.q_index = int(url_index) if url_index.isdigit() else 0
    
    # Asegurar que el índice está en rango
    if st.session_state.q_index >= len(db_questions):
        st.session_state.q_index = 0
    if st.session_state.q_index < 0:
        st.session_state.q_index = 0
    
    # Sincronizar con URL
    st.query_params["q"] = str(st.session_state.q_index)
    
    q = db_questions[st.session_state.q_index]
    
    # --- Barra de progreso ---
    progress = (st.session_state.q_index + 1) / len(db_questions)
    st.progress(progress)
    st.caption(f"Pregunta {st.session_state.q_index + 1} de {len(db_questions)} • Tipo: {q['type'].upper()}")
    
    # --- Mostrar pregunta ---
    st.markdown(f"### {q['question']}")
    
    # --- Contenedor para respuesta ---
    if q['type'] == 'flashcard':
        # Flashcard con efecto 3D flip
        import streamlit.components.v1 as components
        
        question_text = q['question'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
        answer_text = q['answer'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
        
        flip_card_html = f"""
        <style>
            .flip-card {{
                background-color: transparent;
                width: 100%;
                height: 250px;
                perspective: 1000px;
                cursor: pointer;
                margin: 20px 0;
            }}
            .flip-card-inner {{
                position: relative;
                width: 100%;
                height: 100%;
                text-align: center;
                transition: transform 0.8s;
                transform-style: preserve-3d;
            }}
            .flip-card.flipped .flip-card-inner {{
                transform: rotateY(180deg);
            }}
            .flip-card-front, .flip-card-back {{
                position: absolute;
                width: 100%;
                height: 100%;
                -webkit-backface-visibility: hidden;
                backface-visibility: hidden;
                border-radius: 16px;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
                box-sizing: border-box;
                font-size: 18px;
                line-height: 1.5;
            }}
            .flip-card-front {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                box-shadow: 0 8px 32px rgba(102, 126, 234, 0.3);
            }}
            .flip-card-back {{
                background: linear-gradient(135deg, #2c3e50 0%, #4ca1af 100%);
                color: white;
                transform: rotateY(180deg);
                box-shadow: 0 8px 32px rgba(17, 153, 142, 0.3);
            }}
            .flip-hint {{
                text-align: center;
                color: #888;
                font-size: 14px;
                margin-top: 10px;
            }}
        </style>
        <div class="flip-card" onclick="this.classList.toggle('flipped')">
            <div class="flip-card-inner">
                <div class="flip-card-front">
                    <div>
                        <strong>📝 Pregunta</strong><br><br>
                        {question_text}
                    </div>
                </div>
                <div class="flip-card-back">
                    <div>
                        <strong>💡 Respuesta</strong><br><br>
                        {answer_text}
                    </div>
                </div>
            </div>
        </div>
        <p class="flip-hint">👆 Haz clic en la tarjeta para girarla</p>
        """
        
        components.html(flip_card_html, height=320)
        
        # Botones de evaluación
        st.markdown("---")
        st.write("¿Sabías la respuesta?")
        col1, col2 = st.columns(2)
        if col1.button("✅ Sí, lo sabía", use_container_width=True, key=f"know_{q['id']}"):
            save_progress(st.session_state.user, q['id'], True, "Correcto mental")
            if st.session_state.q_index < len(db_questions) - 1:
                st.session_state.q_index += 1
                st.query_params["q"] = str(st.session_state.q_index)
            st.rerun()
        if col2.button("❌ No lo sabía", use_container_width=True, key=f"notknow_{q['id']}"):
            save_progress(st.session_state.user, q['id'], False, "Incorrecto mental")
            if st.session_state.q_index < len(db_questions) - 1:
                st.session_state.q_index += 1
                st.query_params["q"] = str(st.session_state.q_index)
            st.rerun()
    
    elif q['type'] == 'quiz':
        opts = json.loads(q['options']) if isinstance(q['options'], str) else q['options']
        if opts:
            selected_ans = st.radio("Selecciona tu respuesta:", opts, key=f"quiz_{q['id']}")
            if st.button("Comprobar respuesta"):
                is_correct = (selected_ans == q['answer'])
                if is_correct:
                    st.success("🎉 ¡Correcto!")
                else:
                    st.error(f"❌ Incorrecto. La respuesta era: **{q['answer']}**")
                save_progress(st.session_state.user, q['id'], is_correct, selected_ans)
        else:
            st.warning("Esta pregunta no tiene opciones configuradas.")
    
    elif q['type'] == 'essay':
        user_answer = st.text_area("Escribe tu respuesta:", key=f"essay_{q['id']}")
        if st.button("📝 Evaluar con IA"):
            if user_answer.strip():
                with st.spinner("Evaluando..."):
                    result = grade_essay(q['question'], user_answer, q['answer'])
                    if result['correct']:
                        st.success(f"✅ {result['feedback']}")
                    else:
                        st.warning(f"❌ {result['feedback']}")
                        with st.expander("Ver respuesta esperada"):
                            st.write(q['answer'])
                    save_progress(st.session_state.user, q['id'], result['correct'], user_answer)
            else:
                st.warning("Escribe algo primero.")
    
    # --- Navegación: Anterior / Siguiente ---
    st.markdown("---")
    col_prev, col_info, col_next = st.columns([1, 2, 1])
    
    with col_prev:
        if st.button("⬅️ Anterior", use_container_width=True, disabled=(st.session_state.q_index == 0)):
            st.session_state.q_index -= 1
            st.query_params["q"] = str(st.session_state.q_index)
            st.rerun()
    
    with col_info:
        st.markdown(f"<center>📍 {st.session_state.q_index + 1} / {len(db_questions)}</center>", unsafe_allow_html=True)
    
    with col_next:
        if st.button("Siguiente ➡️", use_container_width=True, disabled=(st.session_state.q_index >= len(db_questions) - 1)):
            st.session_state.q_index += 1
            st.query_params["q"] = str(st.session_state.q_index)
            st.rerun()
    
    # --- Botón para ir a pregunta específica ---
    with st.expander("🔢 Ir a pregunta específica"):
        go_to = st.number_input("Número de pregunta:", min_value=1, max_value=len(db_questions), value=st.session_state.q_index + 1)
        if st.button("Ir"):
            st.session_state.q_index = go_to - 1
            st.query_params["q"] = str(st.session_state.q_index)
            st.rerun()

elif mode == "Estadísticas":
    st.header("📊 Tus Estadísticas")
    
    if not supabase:
        st.error("No hay conexión a la base de datos.")
        st.stop()
    
    # Obtener datos del usuario
    progress_res = supabase.table("user_progress").select("*").eq("username", st.session_state.user).execute()
    user_data = progress_res.data
    
    if not user_data:
        st.info("📭 Aún no has respondido ninguna pregunta. ¡Ve a estudiar!")
        st.stop()
    
    # Calcular métricas
    total = len(user_data)
    correct = sum(1 for x in user_data if x.get('is_correct'))
    incorrect = total - correct
    accuracy = (correct / total) * 100 if total > 0 else 0
    
    # --- Métricas principales ---
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Respondidas", total)
    col2.metric("Correctas ✅", correct)
    col3.metric("Precisión", f"{accuracy:.1f}%")
    
    st.markdown("---")
    
    # --- Gráfico de aciertos vs errores ---
    import pandas as pd
    
    chart_data = pd.DataFrame({
        'Resultado': ['Correctas', 'Incorrectas'],
        'Cantidad': [correct, incorrect]
    })
    
    st.subheader("📈 Resumen Visual")
    st.bar_chart(chart_data.set_index('Resultado'))
    
    # --- Historial reciente ---
    st.subheader("🕐 Historial Reciente")
    recent = user_data[-10:]  # Últimas 10
    for entry in reversed(recent):
        icon = "✅" if entry.get('is_correct') else "❌"
        q_id = entry.get('question_id', '?')
        st.write(f"{icon} Pregunta ID: {q_id}")
    
    # --- Botón para limpiar historial ---
    st.markdown("---")
    if st.button("🗑️ Limpiar mi historial", type="secondary"):
        with st.status("Limpiando historial..."):
            supabase.table("user_progress").delete().eq("username", st.session_state.user).execute()
            st.success("Historial eliminado correctamente.")
            time.sleep(1)
            st.rerun()

elif mode == "Gestionar Contenido":
    st.header("⚙️ Gestionar Temas y Preguntas")
    
    if not supabase:
        st.error("No hay conexión a la base de datos.")
        st.stop()
        
    # Obtener todas las preguntas
    res = supabase.table("questions").select("*").execute()
    all_questions = res.data
    
    if not all_questions:
        st.info("No hay contenido para gestionar.")
        st.stop()
        
    # Agrupar por temas
    topics_count = {}
    for q in all_questions:
        t = q.get('topic', 'General')
        topics_count[t] = topics_count.get(t, 0) + 1
        
    st.subheader("📚 Temas actuales")
    
    for t, count in topics_count.items():
        with st.container():
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"""
                <div style="
                    background: linear-gradient(135deg, #667eea22, #764ba222);
                    border-radius: 10px;
                    padding: 15px 20px;
                    margin: 5px 0;
                    border-left: 4px solid #667eea;
                ">
                    <span style="font-size: 18px; font-weight: 600;">📁 {t}</span>
                    <br>
                    <span style="color: #888;">{count} preguntas</span>
                </div>
                """, unsafe_allow_html=True)
            with col2:
                st.write("") # Spacing
                if st.button("🗑️ Eliminar", key=f"del_topic_{t}", use_container_width=True):
                    with st.spinner(f"Eliminando tema '{t}'..."):
                        q_ids = [q['id'] for q in all_questions if q.get('topic') == t]
                        
                        if q_ids:
                            supabase.table("user_progress").delete().in_("question_id", q_ids).execute()
                            supabase.table("questions").delete().eq("topic", t).execute()
                            
                        st.success(f"Tema '{t}' eliminado correctamente.")
                        st.rerun()
                
    st.markdown("---")
    with st.expander("⚠️ Zona de Peligro"):
        if st.button("🧨 Eliminar TODAS las preguntas", type="primary"):
            with st.spinner("Eliminando todo..."):
                supabase.table("user_progress").delete().neq("username", "---").execute() # Borrar todo el progreso
                supabase.table("questions").delete().neq("topic", "---").execute() # Borrar todas las preguntas
                st.success("Toda la base de datos ha sido limpiada.")
                st.rerun()