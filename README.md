# 🎓 Study AI Pro

Aplicación de flashcards inteligentes con IA para estudiar de forma efectiva.

## Características

- 📤 Sube PDFs o PowerPoints y genera preguntas automáticamente con Gemini AI
- 🃏 Flashcards con efecto 3D flip
- 📝 Preguntas tipo quiz con corrección automática
- ✍️ Preguntas de desarrollo evaluadas por IA
- 📊 Estadísticas de progreso personal
- 🔄 Modo repaso de preguntas falladas

## Requisitos

- Python 3.10+
- Cuenta en [Supabase](https://supabase.com) (gratis)
- API Key de [Google AI Studio](https://aistudio.google.com) (gratis)

## Instalación local

1. Clona el repositorio
2. Crea un entorno virtual:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate  # Windows
   source venv/bin/activate  # Linux/Mac
   ```
3. Instala dependencias:
   ```bash
   pip install -r requirements.txt
   ```
4. Crea `.streamlit/secrets.toml` con tus credenciales:
   ```toml
   SUPABASE_URL = "tu-url-de-supabase/"
   SUPABASE_KEY = "tu-api-key-de-supabase"
   GOOGLE_API_KEY = "tu-api-key-de-gemini"
   ```
5. Ejecuta:
   ```bash
   streamlit run app.py
   ```

## Base de datos Supabase

Necesitas crear estas tablas en tu proyecto Supabase:

### Tabla `questions`
| Columna | Tipo | Notas |
|---------|------|-------|
| id | int8 | Primary key, auto-increment |
| topic | text | |
| type | text | flashcard, quiz, essay |
| question | text | |
| answer | text | |
| options | jsonb | Nullable, para quiz |
| created_at | timestamptz | Default: now() |

### Tabla `user_progress`
| Columna | Tipo | Notas |
|---------|------|-------|
| id | int8 | Primary key, auto-increment |
| username | text | |
| question_id | int8 | Foreign key a questions.id |
| is_correct | bool | |
| user_answer | text | |
| created_at | timestamptz | Default: now() |

### Storage Bucket
Crea un bucket llamado `documents` (público) para almacenar los archivos subidos.

## Despliegue en Streamlit Cloud

1. Sube este repo a GitHub
2. Ve a [share.streamlit.io](https://share.streamlit.io)
3. Conecta tu cuenta de GitHub
4. Selecciona el repositorio
5. En "Advanced settings" > "Secrets", pega tus credenciales:
   ```toml
   SUPABASE_URL = "tu-url/"
   SUPABASE_KEY = "tu-key"
   GOOGLE_API_KEY = "tu-key"
   ```
6. ¡Deploy!

## Licencia

MIT
