const getSubtitlesBtn = document.getElementById('getSubtitles');
const progressContainer = document.getElementById('progressContainer');
const progressBar = document.getElementById('progressBar');
const subtitleDisplay = document.getElementById('subtitleDisplay');
const downloadSrtBtn = document.getElementById('downloadSrt');
const copyTextBtn = document.getElementById('copyText');
const viewTimestampedBtn = document.getElementById('viewTimestamped');
const viewScriptBtn = document.getElementById('viewScript');

let transcriptionText = '';
let timestamps = [];
let showingTimestamps = false;

function segundosASRTFormat(seconds) {
  const h = Math.floor(seconds / 3600).toString().padStart(2, '0');
  const m = Math.floor((seconds % 3600) / 60).toString().padStart(2, '0');
  const s = Math.floor(seconds % 60).toString().padStart(2, '0');
  const ms = Math.floor((seconds % 1) * 1000).toString().padStart(3, '0');
  return `${h}:${m}:${s},${ms}`;
}

function generarSRT(timestamps) {
  let srt = '';
  let index = 1;
  for (let i = 0; i < timestamps.length; i += 5) {
    const grupo = timestamps.slice(i, i + 5);
    const start = grupo[0].start_time;
    const end = grupo[grupo.length - 1].end_time;
    const texto = grupo.map((w) => w.word).join(' ');
    srt += `${index}\n${segundosASRTFormat(start)} --> ${segundosASRTFormat(end)}\n${texto}\n\n`;
    index++;
  }
  return srt;
}

function nombreIdioma(code) {
  const map = {
    'es-419': 'Español Latino',
    'es-ES': 'Español (España)',
    'en-US': 'Inglés (EE. UU.)',
    'en-GB': 'Inglés (Reino Unido)',
  };
  return map[code] || code;
}

getSubtitlesBtn.addEventListener('click', async () => {
  const videoUrl = document.getElementById('videoUrl').value.trim();
  if (!videoUrl) {
    alert('Por favor, ingresa una URL válida');
    return;
  }
  progressContainer.style.display = 'block';
  progressBar.removeAttribute('value');
  subtitleDisplay.textContent = 'Transcribiendo, por favor espera...';

  try {
    const response = await fetch('/transcribir', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: videoUrl }),
    });
    if (!response.ok) throw new Error('Error en la transcripción');

    const data = await response.json();
    if (data.error) {
      subtitleDisplay.textContent = 'Error: ' + data.error;
      downloadSrtBtn.style.display = 'none';
      copyTextBtn.style.display = 'none';
      viewTimestampedBtn.style.display = 'none';
      viewScriptBtn.style.display = 'none';
      return;
    }

    transcriptionText = data.transcription || '';
    timestamps = data.timestamps || [];
    const detectedLanguage = nombreIdioma(data.language || 'es-419');

    subtitleDisplay.textContent = `[Idioma detectado: ${detectedLanguage}] \n\n${transcriptionText}`;

    downloadSrtBtn.style.display = 'inline-block';
    copyTextBtn.style.display = 'inline-block';
    viewTimestampedBtn.style.display = 'inline-block';
    viewScriptBtn.style.display = 'inline-block';

    showingTimestamps = false;
  } catch (error) {
    subtitleDisplay.textContent = 'Error al conectarse al servidor: ' + error.message;
  } finally {
    progressBar.value = 100;
  }
});

viewTimestampedBtn.addEventListener('click', () => {
  if (!timestamps.length) {
    alert('No hay datos de timestamps para mostrar.');
    return;
  }
  showingTimestamps = true;

  const bloqueSegundos = 10;
  let bloques = [];
  let textoBloque = '';
  let inicioBloque = timestamps[0].start_time;
  let finBloque = inicioBloque;

  timestamps.forEach(({ word, start_time }) => {
    if (start_time - inicioBloque <= bloqueSegundos) {
      textoBloque += word + ' ';
      finBloque = start_time;
    } else {
      bloques.push(`[${Math.floor(inicioBloque / 60)}:${Math.floor(inicioBloque % 60).toString().padStart(2,'0')} - ${Math.floor(finBloque / 60)}:${Math.floor(finBloque % 60).toString().padStart(2,'0')}]\n${textoBloque.trim()}`);
      inicioBloque = start_time;
      finBloque = start_time;
      textoBloque = word + ' ';
    }
  });

  if (textoBloque) {
    bloques.push(`[${Math.floor(inicioBloque / 60)}:${Math.floor(inicioBloque % 60).toString().padStart(2,'0')} - ${Math.floor(finBloque / 60)}:${Math.floor(finBloque % 60).toString().padStart(2,'0')}]\n${textoBloque.trim()}`);
  }

  subtitleDisplay.textContent = bloques.join('\n\n');
});

viewScriptBtn.addEventListener('click', () => {
  showingTimestamps = false;
  subtitleDisplay.textContent = transcriptionText;
});

downloadSrtBtn.addEventListener('click', () => {
  if (!timestamps.length) {
    alert('No hay datos para generar el archivo SRT.');
    return;
  }
  const srtContent = generarSRT(timestamps);
  const blob = new Blob([srtContent], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'subtitulos.srt';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, 100);
});

copyTextBtn.addEventListener('click', () => {
  if (!transcriptionText) {
    alert('No hay texto para copiar.');
    return;
  }
  navigator.clipboard.writeText(transcriptionText);
  alert('Texto copiado al portapapeles');
});