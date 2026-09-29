import os
import json
import asyncio
import requests
import streamlit as st
import edge_tts
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from google import genai
from groq import Groq
from streamlit_mic_recorder import mic_recorder

# Silenzia i log interni di pypdf sugli oggetti corrotti
import logging
logging.getLogger("pypdf").setLevel(logging.ERROR)

# Prova ad importare pypdf per la lettura dei file PDF
try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False


# --- CONFIGURAZIONE PAGINA STREAMLIT ---
st.set_page_config(
    page_title="F.R.I.D.A.Y. - Neural Interface & Assistant",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CHIAVI E CREDENZIALI ---
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", "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")
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "AQ.Ab8RN6LA3QjRLLpAtOSMee8l3AbEPppXTC0o5pJ1FTJa6phgvg")

SPOTIPY_CLIENT_ID = st.secrets.get("SPOTIPY_CLIENT_ID", "85c4ccfaa4044c8089f876900941f796")
SPOTIPY_CLIENT_SECRET = st.secrets.get("SPOTIPY_CLIENT_SECRET", "aee96b979be34f1280c987c5a8628aa8")
SPOTIPY_REDIRECT_URI = st.secrets.get("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SCOPE = "user-modify-playback-state user-read-playback-state"


# --- INIZIALIZZAZIONE CLIENT SICURA ---
def get_groq_client():
    if GROQ_API_KEY:
        try:
            return Groq(api_key=GROQ_API_KEY)
        except Exception as e:
            print(f"Errore inizializzazione Groq: {e}")
    return None

client_groq = get_groq_client()

sp = None
try:
    if SPOTIPY_CLIENT_ID:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=SPOTIPY_CLIENT_ID,
            client_secret=SPOTIPY_CLIENT_SECRET,
            redirect_uri=SPOTIPY_REDIRECT_URI,
            scope=SCOPE
        ))
except Exception as e:
    print(f"Avviso configurazione Spotify: {e}")


# --- GESTIONE MEMORIA PERSISTENTE (JSON) ---
class FridayMemoryManager:
    def __init__(self, file_memoria="memoria_friday.json"):
        self.file_memoria = file_memoria
        self.memoria = self.carica_memoria()

    def carica_memoria(self) -> dict:
        if os.path.exists(self.file_memoria):
            try:
                with open(self.file_memoria, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Errore caricamento memoria: {e}")
        return {"ricordi": [], "preferenze": {}}

    def salva_memoria(self):
        try:
            with open(self.file_memoria, "w", encoding="utf-8") as f:
                json.dump(self.memoria, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Errore salvataggio memoria: {e}")

    def aggiungi_ricordo(self, testo_ricordo: str):
        testo_clean = testo_ricordo.strip()
        if testo_clean and testo_clean not in self.memoria["ricordi"]:
            self.memoria["ricordi"].append(testo_clean)
            self.salva_memoria()
            return True
        return False

    def ottieni_testo_memoria(self) -> str:
        if not self.memoria["ricordi"]:
            return "Nessun ricordo memorizzato al momento."
        return "\n".join([f"- {r}" for r in self.memoria["ricordi"]])

    def svuota_memoria(self):
        self.memoria["ricordi"] = []
        self.salva_memoria()


# --- GESTIONE APPUNTI DI STUDIO (GEMINI API) ---
class FridayNotebookManager:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = None
        self.cartella_appunti = "appunti_notebook"
        os.makedirs(self.cartella_appunti, exist_ok=True)
        
        if api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"Errore inizializzazione Gemini Client: {e}")

    def _estrai_testo_pdf(self, uploaded_file) -> str:
        if not PYPDF_AVAILABLE:
            return "[Impossibile leggere il PDF: installa pypdf]"
        try:
            reader = pypdf.PdfReader(uploaded_file, strict=False)
            testo = ""
            for i, page in enumerate(reader.pages):
                try:
                    t = page.extract_text()
                    if t:
                        testo += f"\n--- Pagina {i+1} ---\n" + t
                except Exception as p_err:
                    print(f"Avviso lettura pagina {i+1}: {p_err}")
            return testo
        except Exception as e:
            print(f"Errore lettura PDF: {e}")
            return ""

    def aggiungi_file_appunti_streamlit(self, uploaded_file) -> bool:
        try:
            nome_file = uploaded_file.name
            estensione = os.path.splitext(nome_file)[1].lower()
            
            if estensione == ".pdf":
                contenuto = self._estrai_testo_pdf(uploaded_file)
                nome_salvato = os.path.splitext(nome_file)[0] + ".txt"
            else:
                contenuto = uploaded_file.read().decode("utf-8", errors="ignore")
                nome_salvato = nome_file

            if not contenuto.strip():
                return False

            destinazione = os.path.join(self.cartella_appunti, nome_salvato)
            with open(destinazione, "w", encoding="utf-8") as f_out:
                f_out.write(contenuto)
            return True
        except Exception as e:
            print(f"Errore salvataggio appunti: {e}")
            return False

    def elencafiles(self) -> list:
        if not os.path.exists(self.cartella_appunti):
            return []
        return [f for f in os.listdir(self.cartella_appunti) if os.path.isfile(os.path.join(self.cartella_appunti, f))]

    def svuota_appunti(self) -> bool:
        try:
            for f in self.elencafiles():
                os.remove(os.path.join(self.cartella_appunti, f))
            return True
        except Exception as e:
            print(f"Errore nello svuotamento appunti: {e}")
            return False

    def cerca_negli_appunti(self, quesito: str) -> str:
        if not self.client:
            return ""
        
        file_list = self.elencafiles()
        if not file_list:
            return ""

        testo_appunti = ""
        for nome_f in file_list:
            path_f = os.path.join(self.cartella_appunti, nome_f)
            try:
                with open(path_f, "r", encoding="utf-8", errors="ignore") as f:
                    testo_appunti += f"\n\n=== FONTE: {nome_f} ===\n" + f.read()
            except Exception as e:
                print(f"Errore lettura file {nome_f}: {e}")

        if not testo_appunti.strip():
            return ""

        try:
            prompt = f"""
            Sei l'assistente virtuale F.R.I.D.A.Y. Analizza i seguenti appunti di studio di Francesca e rispondi alla sua domanda in modo chiaro, preciso e strutturato.

            Domanda di Francesca: {quesito}

            [APPUNTI DI STUDIO]:
            {testo_appunti}
            """
            
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            return response.text
        except Exception as e:
            print(f"Errore analisi Gemini: {e}")
            return ""

    def genera_quiz_studio(self) -> str:
        if not self.client:
            return "Servizio Gemini non inizializzato."
        
        file_list = self.elencafiles()
        if not file_list:
            return "Nessun appunto disponibile nei tuoi quaderni per generare il quiz."

        testo_appunti = ""
        for nome_f in file_list:
            path_f = os.path.join(self.cartella_appunti, nome_f)
            try:
                with open(path_f, "r", encoding="utf-8", errors="ignore") as f:
                    testo_appunti += f"\n{f.read()}"
            except Exception:
                pass

        try:
            prompt = f"""
            Basandoti sui seguenti appunti di studio, crea un test/quiz di verifica per Francesca composto da 3 domande a risposta multipla (con opzioni A, B, C) e fornisci le risposte corrette spiegate in fondo.

            Appunti:
            {testo_appunti[:4000]}
            """
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            return response.text
        except Exception as e:
            return f"Errore generazione quiz: {e}"


# --- SYSTEM PROMPT ---
def crea_system_prompt(memoria_mgr: FridayMemoryManager) -> str:
    ricordi_txt = memoria_mgr.ottieni_testo_memoria()
    return f"""
Sei F.R.I.D.A.Y., l'assistente virtuale personale creata appositamente da Jacopo per la sua ragazza, Francesca.
Sei brillante, efficiente, premurosa e hai un tocco di ironia elegante e cordiale.

[MEMORIA DI FRANCESCA (INFORMAZIONI E PREFERENZE SALVATE)]:
{ricordi_txt}

REGOLE DI COMPORTAMENTO:
1. Rivolgiti all'utente chiamandola per nome (Francesca) quando appropriato e naturale.
2. Usa le informazioni in memoria per personalizzare la conversazione quando pertinente.
3. Se ti vengono forniti dati meteo, analizzali e fornisci sempre un consiglio pratico e stiloso sull'outfit per Francesca.
4. Se ti vengono forniti appunti/estratto di studio, utilizzali per spiegare il concetto richiesto in modo chiaro, preciso e strutturato.
5. Se mostra stanchezza, ansia da studio, bisogno di carica o dolcezza, assumi un tono particolarmente affettuoso ed empatico.
6. Rispondi sempre in modo sintetico e diretto (massimo 2-4 frasi).
"""


# --- FUNZIONE METEO ---
def ottieni_meteo_e_outfit(citta="Napoli"):
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={citta}&count=1&language=it&format=json"
        geo_res = requests.get(geo_url, timeout=5).json()

        if not geo_res.get("results"):
            return f"Non sono riuscita a trovare la posizione per {citta}."

        lat = geo_res["results"][0]["latitude"]
        lon = geo_res["results"][0]["longitude"]

        meteo_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        meteo_res = requests.get(meteo_url, timeout=5).json()
        current = meteo_res.get("current_weather", {})

        temp = current.get("temperature")
        wind = current.get("windspeed")
        code = current.get("weathercode")

        m_code = {
            0: "Sereno", 1: "Prevalentemente sereno", 2: "Parzialmente nuvoloso", 3: "Coperto",
            45: "Nebbia", 51: "Pioggerellina", 61: "Pioggia", 71: "Neve", 80: "Rovesci di pioggia", 95: "Temporale"
        }
        condizione = m_code.get(code, "Nuvoloso")

        return f"Meteo attuale a {citta}: {temp}°C, {condizione}, vento a {wind} km/h."
    except Exception as e:
        return f"Errore nel recupero dati meteo: {e}"


# --- SINTESI VOCALE EDGE-TTS PER BROWSER/IPAD ---
async def _generate_audio_async(text, rate):
    filename = "temp_voice.mp3"
    communicate = edge_tts.Communicate(text, "it-IT-ElsaNeural", rate=rate)
    await communicate.save(filename)
    return filename

def genera_audio_voce(testo, velocita="+0%"):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        file_path = loop.run_until_complete(_generate_audio_async(testo, velocita))
        loop.close()
        
        if os.path.exists(file_path):
            with open(file_path, "rb") as f:
                audio_bytes = f.read()
            os.remove(file_path)
            return audio_bytes
    except Exception as e:
        print(f"Errore TTS: {e}")
    return None


# --- INIZIALIZZAZIONE OGGETTI IN SESSIONE ---
if "memory_mgr" not in st.session_state:
    st.session_state.memory_mgr = FridayMemoryManager()

if "notebook_mgr" not in st.session_state:
    st.session_state.notebook_mgr = FridayNotebookManager(api_key=GEMINI_API_KEY)

if "messages" not in st.session_state:
    init_msg = "Sistemi online e memoria caricata. Benvenuta Francesca! I tuoi quaderni di studio e i tuoi ricordi sono pronti."
    audio_init = genera_audio_voce(init_msg)
    st.session_state.messages = [{
        "role": "assistant",
        "content": init_msg,
        "audio": audio_init
    }]

if "velocita_voce" not in st.session_state:
    st.session_state.velocita_voce = "+0%"


# --- INTERFACCIA UTENTE (STREAMLIT) ---
st.title("⚡ F.R.I.D.A.Y. // STARK NEURAL SYSTEM")
st.caption("Interfaccia Intelligente per Francesca • Studio, Memoria & Assistente AI")

# --- SIDEBAR (PANNELLO CONTROLLI) ---
with st.sidebar:
    st.header("🎤 Comando Vocale")
    audio_recorded = mic_recorder(
        start_prompt="🎤 Avvia Ascolto",
        stop_prompt="⏹️ Ferma e Invia",
        key="recorder"
    )

    if audio_recorded and "bytes" in audio_recorded and audio_recorded["bytes"]:
        c_groq = get_groq_client()
        if not c_groq:
            st.error("Chiave GROQ_API_KEY non trovata o non valida.")
        else:
            try:
                audio_bytes_in = audio_recorded["bytes"]
                with open("temp_audio.wav", "wb") as f:
                    f.write(audio_bytes_in)
                
                with open("temp_audio.wav", "rb") as file:
                    transcription = c_groq.audio.transcriptions.create(
                        file=(file.name, file.read()),
                        model="whisper-large-v3",
                        language="it"
                    )
                
                testo_trascritto = transcription.text
                st.success(f"Trascritto: {testo_trascritto}")
                st.session_state["paz_prompt"] = testo_trascritto
                
                if os.path.exists("temp_audio.wav"):
                    os.remove("temp_audio.wav")
                st.rerun()
            except Exception as err_voice:
                st.error(f"Errore trascrizione: {err_voice}")

    st.divider()

    st.header("⚙️ Impostazioni Voce")
    scelta_vel = st.selectbox("Velocità Lettura F.R.I.D.A.Y.:", ["Normale", "Veloce (+20%)", "Lenta (-15%)"])
    if "Veloce" in scelta_vel:
        st.session_state.velocita_voce = "+20%"
    elif "Lenta" in scelta_vel:
        st.session_state.velocita_voce = "-15%"
    else:
        st.session_state.velocita_voce = "+0%"

    st.divider()

    st.header("🧠 Memoria Ricordi")
    nuovo_ricordo = st.text_input("Aggiungi ricordo/preferenza:")
    if st.button("💾 Salva Ricordo", use_container_width=True):
        if nuovo_ricordo and st.session_state.memory_mgr.aggiungi_ricordo(nuovo_ricordo):
            st.success(f"Ricordo salvato!")
        elif nuovo_ricordo:
            st.info("Questo ricordo è già in memoria.")

    with st.expander("📜 Visualizza Ricordi"):
        st.text(st.session_state.memory_mgr.ottieni_testo_memoria())
        if st.button("🗑️ Svuota Memoria", use_container_width=True):
            st.session_state.memory_mgr.svuota_memoria()
            st.warning("Memoria azzerata.")

    st.divider()

    st.header("📚 Quaderno Appunti Studio")
    uploaded_files = st.file_uploader("Carica dispense (.txt, .md, .pdf):", accept_multiple_files=True)
    if uploaded_files:
        for uf in uploaded_files:
            if st.session_state.notebook_mgr.aggiungi_file_appunti_streamlit(uf):
                st.success(f"Caricato: {uf.name}")

    file_appunti = st.session_state.notebook_mgr.elencafiles()
    with st.expander(f"📁 Appunti Caricati ({len(file_appunti)})"):
        if file_appunti:
            for f in file_appunti:
                st.write(f"• {f}")
            if st.button("🗑️ Svuota Quaderno", use_container_width=True):
                st.session_state.notebook_mgr.svuota_appunti()
                st.warning("Appunti cancellati.")
        else:
            st.write("Nessun file presente.")


# --- AZIONI RAPIDE (PULSANTI HUD) ---
col1, col2, col3, col4 = st.columns(4)

with col1:
    if st.button("🌤️ Meteo Rapido", use_container_width=True):
        st.session_state["paz_prompt"] = "Come devo vestirmi oggi in base al meteo?"

with col2:
    if st.button("💖 Carica & Dolcezze", use_container_width=True):
        st.session_state["paz_prompt"] = "Friday, ho bisogno di carica e dolcezze prima di continuare a studiare!"

with col3:
    if st.button("🎵 Spotify Studio", use_container_width=True):
        st.markdown("[👉 Apri Playlist Spotify Studio](https://open.spotify.com/playlist/37i9dQZF1DX8NTLI29BXZa)")

with col4:
    if st.button("📝 Genera Quiz Studio", use_container_width=True):
        st.session_state["paz_quiz"] = True


# --- MOSTRA CHAT E AUDIO ---
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "audio" in msg and msg["audio"]:
            st.audio(msg["audio"], format="audio/mp3")


# --- DEFINIZIONE AZIONE DI INVIO/ELABORAZIONE ---
prompt_user = st.chat_input("Fai una domanda di studio o parla con F.R.I.D.A.Y....")

# Gestione trigger dai pulsanti rapidi o comando vocale
if "paz_prompt" in st.session_state:
    prompt_user = st.session_state.pop("paz_prompt")

is_quiz = False
if "paz_quiz" in st.session_state:
    st.session_state.pop("paz_quiz")
    is_quiz = True


if prompt_user or is_quiz:
    if is_quiz:
        prompt_user = "Genera un quiz sui miei appunti di studio."

    # 1. Mostra il messaggio dell'utente
    st.session_state.messages.append({"role": "user", "content": prompt_user})
    with st.chat_message("user"):
        st.markdown(prompt_user)

    # 2. Costruisci il sistema di prompt
    system_prompt = crea_system_prompt(st.session_state.memory_mgr)
    cronologia = [{"role": "system", "content": system_prompt}]
    
    for m in st.session_state.messages[-8:]:
        cronologia.append({"role": m["role"], "content": m["content"]})

    testo_lower = prompt_user.lower()
    
    # Controllo salvataggio memoria automatico
    parole_memoria = ["ricorda", "memorizza", "tieni a mente", "salva questo"]
    if any(p in testo_lower for p in parole_memoria):
        for p in parole_memoria:
            if p in testo_lower:
                ricordo_estratto = prompt_user.lower().split(p)[-1].strip()
                if ricordo_estratto:
                    st.session_state.memory_mgr.aggiungi_ricordo(ricordo_estratto)

    # Client Groq per la chat
    c_groq = get_groq_client()

    # Elaborazione risposta speciale (Quiz, Appunti, Meteo)
    if is_quiz:
        risposta = st.session_state.notebook_mgr.genera_quiz_studio()
    elif any(p in testo_lower for p in ["studio", "spiega", "appunti", "esame", "concetto", "capitolo", "riassunto"]):
        res_appunti = st.session_state.notebook_mgr.cerca_negli_appunti(prompt_user)
        if res_appunti:
            cronologia.append({
                "role": "user",
                "content": f"Domanda di Francesca: {prompt_user}\n\nEstratto Appunti:\n{res_appunti}"
            })
        else:
            cronologia.append({"role": "user", "content": prompt_user})
        
        if c_groq:
            response = c_groq.chat.completions.create(model="openai/gpt-oss-120b", messages=cronologia)
            risposta = response.choices[0].message.content
        else:
            risposta = "Errore: GROQ_API_KEY non trovata."
            
    elif any(p in testo_lower for p in ["meteo", "tempo", "vestirmi", "outfit", "temperatura", "ombrello", "pioggia"]):
        info_m = ottieni_meteo_e_outfit("Napoli")
        cronologia.append({"role": "user", "content": f"Meteo attuale: {info_m}. Consiglia un outfit a Francesca."})
        if c_groq:
            response = c_groq.chat.completions.create(model="openai/gpt-oss-120b", messages=cronologia)
            risposta = response.choices[0].message.content
        else:
            risposta = "Errore: GROQ_API_KEY non trovata."
    else:
        if c_groq:
            response = c_groq.chat.completions.create(model="openai/gpt-oss-120b", messages=cronologia)
            risposta = response.choices[0].message.content
        else:
            risposta = "Errore: GROQ_API_KEY non trovata."

    # 3. Genera Audio Voce ed Eroga in Output
    audio_bytes = genera_audio_voce(risposta, velocita=st.session_state.velocita_voce)

    with st.chat_message("assistant"):
        st.markdown(risposta)
        if audio_bytes:
            st.audio(audio_bytes, format="audio/mp3", autoplay=True)

    # 4. Salva in cronologia sessione
    st.session_state.messages.append({
        "role": "assistant",
        "content": risposta,
        "audio": audio_bytes
    })
