const els = {
  camera: document.querySelector('#camera'), preview: document.querySelector('#preview'), empty: document.querySelector('#emptyPreview'),
  startCamera: document.querySelector('#startCamera'), takePhoto: document.querySelector('#takePhoto'), input: document.querySelector('#imageInput'), analyze: document.querySelector('#analyze'),
  sensorValue: document.querySelector('#sensorValue'), sensorTime: document.querySelector('#sensorTime'), sensorDot: document.querySelector('#sensorDot'), gasSignal: document.querySelector('#gasSignal'), sparkline: document.querySelector('#sparkline'),
  resultEmpty: document.querySelector('#resultEmpty'), resultCard: document.querySelector('#resultCard'), resultImage: document.querySelector('#resultImage'),
  resultLabel: document.querySelector('#resultLabel'), resultDetail: document.querySelector('#resultDetail'), resultConfidence: document.querySelector('#resultConfidence'), visualScore: document.querySelector('#visualScore'), usedSensor: document.querySelector('#usedSensor'), sensorIndex: document.querySelector('#sensorIndex'), resultGasSignal: document.querySelector('#resultGasSignal'),
  history: document.querySelector('#history'), toast: document.querySelector('#toast')
};
let selectedFile = null, stream = null;

function toast(message) { els.toast.textContent = message; els.toast.classList.add('show'); setTimeout(() => els.toast.classList.remove('show'), 3500); }
function localTime(iso) { return new Date(iso).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}); }
async function readApi(response) {
  const raw = await response.text();
  try { return JSON.parse(raw); }
  catch {
    throw new Error(`The server returned HTTP ${response.status}, not an API response. Restart the website with start.bat and open http://127.0.0.1:5000.`);
  }
}
function showPreview(file) { selectedFile = file; els.preview.src = URL.createObjectURL(file); els.preview.classList.add('visible'); els.empty.classList.add('hidden'); els.camera.classList.remove('visible'); els.analyze.disabled = false; }

function gasSignalFromRaw(raw) {
  if (raw === null || raw === undefined) return '-';
  const index = (raw / 4095) * 100;
  if (index < 25) return 'Normal';
  if (index < 50) return 'Elevated';
  return 'High';
}

els.input.addEventListener('change', () => { if (els.input.files[0]) { stopCamera(); showPreview(els.input.files[0]); }});
els.startCamera.addEventListener('click', async () => {
  try { stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}}, audio:false}); els.camera.srcObject = stream; await els.camera.play(); els.camera.classList.add('visible'); els.preview.classList.remove('visible'); els.empty.classList.add('hidden'); els.takePhoto.classList.remove('hidden'); els.startCamera.textContent = 'Camera on'; } catch { toast('Camera access was unavailable. You can still upload an image.'); }
});
els.takePhoto.addEventListener('click', () => { const canvas = document.createElement('canvas'); canvas.width=els.camera.videoWidth; canvas.height=els.camera.videoHeight; canvas.getContext('2d').drawImage(els.camera,0,0); canvas.toBlob(blob => { showPreview(new File([blob], `banana-capture-${Date.now()}.jpg`, {type:'image/jpeg'})); stopCamera(); }, 'image/jpeg', .9); });
function stopCamera(){ if(stream) stream.getTracks().forEach(track=>track.stop()); stream=null; els.takePhoto.classList.add('hidden'); els.startCamera.textContent='Open camera'; }

async function refreshSensor() {
  try { const response = await fetch('/api/sensor/latest'); const data = await readApi(response); if (!response.ok) throw new Error(data.error || 'Sensor service is unavailable.'); const latest = data.latest;
    const raw = latest ? latest.mq135 : null;
    els.sensorValue.textContent = raw ?? '-'; els.sensorTime.textContent = latest ? `Last received ${localTime(latest.received_at)}` : 'No reading received yet. Start the ESP32 to connect.';
    els.sensorDot.textContent = latest ? 'LIVE' : 'WAITING'; els.sensorDot.style.background = latest ? '#6f9d50' : '#456b4e'; els.gasSignal.textContent = gasSignalFromRaw(raw); drawSparkline(data.history || []);
  } catch { els.sensorTime.textContent = 'Dashboard cannot reach the local sensor service.'; els.gasSignal.textContent = '-'; }
}
function drawSparkline(history) { if (!history.length) { els.sparkline.innerHTML=''; return; } const values=history.map(x=>x.mq135), lo=Math.min(...values), hi=Math.max(...values), range=Math.max(hi-lo,20); const points=values.map((v,i)=>`${(i/(Math.max(values.length-1,1))*300).toFixed(1)},${(58-((v-lo)/range)*43).toFixed(1)}`).join(' '); els.sparkline.innerHTML=`<polyline points="${points}" fill="none" stroke="#dff25d" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><line x1="0" y1="61" x2="300" y2="61" stroke="#80a887" stroke-opacity=".4"/>`; }

els.analyze.addEventListener('click', async () => { if(!selectedFile) return; els.analyze.disabled=true; els.analyze.textContent='Inspecting...'; const form=new FormData(); form.append('image', selectedFile); try { const response=await fetch('/api/analyze',{method:'POST',body:form}); const data=await readApi(response); if(!response.ok) throw new Error(data.error || 'Inspection failed.'); showResult(data); refreshHistory(); toast('Inspection saved to the local log.'); } catch(error){toast(error.message)} finally { els.analyze.disabled=false; els.analyze.innerHTML='Run freshness inspection <span>-></span>'; } });
function showResult(data) { els.resultEmpty.classList.add('hidden'); els.resultCard.classList.remove('hidden'); els.resultImage.src=data.image_url; els.resultLabel.textContent=data.label; els.resultDetail.textContent=data.model_status; els.resultConfidence.textContent=`${data.confidence}%`; els.visualScore.textContent=`${data.visual_score}/100`; els.usedSensor.textContent=data.mq135 ?? 'No live value'; els.sensorIndex.textContent=data.sensor_index === null ? '-' : `${data.sensor_index}/100`; els.resultGasSignal.textContent=gasSignalFromRaw(data.mq135); }
async function refreshHistory(){ try { const response=await fetch('/api/inspections'); const data=await readApi(response); if(!response.ok) throw new Error(data.error || 'History is unavailable.'); if(!data.items.length){els.history.innerHTML='<p class="history-empty">No inspections yet.</p>';return;} els.history.innerHTML=data.items.map(item=>`<article class="history-item"><img src="/uploads/${encodeURIComponent(item.image_name)}" alt="Inspection ${item.id}"><b>${item.result_label}</b><p>${item.confidence}% - ${item.mq135 ?? 'no MQ135'} - ${gasSignalFromRaw(item.mq135)}</p><p>${new Date(item.created_at).toLocaleString()}</p></article>`).join(''); } catch { els.history.innerHTML='<p class="history-empty">Could not load inspection history.</p>'; }}
document.querySelector('#refreshHistory').addEventListener('click', refreshHistory);
refreshSensor(); refreshHistory(); setInterval(refreshSensor, 2000);
