const els = {
  camera: document.querySelector('#camera'), preview: document.querySelector('#preview'), empty: document.querySelector('#emptyPreview'),
  startCamera: document.querySelector('#startCamera'), takePhoto: document.querySelector('#takePhoto'), input: document.querySelector('#imageInput'), analyze: document.querySelector('#analyze'),
  resultEmpty: document.querySelector('#resultEmpty'), resultCard: document.querySelector('#resultCard'), resultImage: document.querySelector('#resultImage'),
  resultLabel: document.querySelector('#resultLabel'), resultDetail: document.querySelector('#resultDetail'), resultConfidence: document.querySelector('#resultConfidence'), visualScore: document.querySelector('#visualScore'),
  history: document.querySelector('#history'), toast: document.querySelector('#toast')
};
let selectedFile = null, stream = null;

function toast(message) { els.toast.textContent = message; els.toast.classList.add('show'); setTimeout(() => els.toast.classList.remove('show'), 3500); }
async function readApi(response) {
  const raw = await response.text();
  try { return JSON.parse(raw); }
  catch {
    throw new Error(`The server returned HTTP ${response.status}, not an API response. Restart the website with start.bat and open http://127.0.0.1:5000.`);
  }
}
function showPreview(file) { selectedFile = file; els.preview.src = URL.createObjectURL(file); els.preview.classList.add('visible'); els.empty.classList.add('hidden'); els.camera.classList.remove('visible'); els.analyze.disabled = false; }

els.input.addEventListener('change', () => { if (els.input.files[0]) { stopCamera(); showPreview(els.input.files[0]); }});
els.startCamera.addEventListener('click', async () => {
  try { stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}}, audio:false}); els.camera.srcObject = stream; await els.camera.play(); els.camera.classList.add('visible'); els.preview.classList.remove('visible'); els.empty.classList.add('hidden'); els.takePhoto.classList.remove('hidden'); els.startCamera.textContent = 'Camera on'; } catch { toast('Camera access was unavailable. You can still upload an image.'); }
});
els.takePhoto.addEventListener('click', () => { const canvas = document.createElement('canvas'); canvas.width=els.camera.videoWidth; canvas.height=els.camera.videoHeight; canvas.getContext('2d').drawImage(els.camera,0,0); canvas.toBlob(blob => { showPreview(new File([blob], `banana-capture-${Date.now()}.jpg`, {type:'image/jpeg'})); stopCamera(); }, 'image/jpeg', .9); });
function stopCamera(){ if(stream) stream.getTracks().forEach(track=>track.stop()); stream=null; els.takePhoto.classList.add('hidden'); els.startCamera.textContent='Open camera'; }

els.analyze.addEventListener('click', async () => { if(!selectedFile) return; els.analyze.disabled=true; els.analyze.textContent='Inspecting...'; const form=new FormData(); form.append('image', selectedFile); try { const response=await fetch('/api/analyze',{method:'POST',body:form}); const data=await readApi(response); if(!response.ok) throw new Error(data.error || 'Inspection failed.'); showResult(data); refreshHistory(); toast('Inspection saved to the local log.'); } catch(error){toast(error.message)} finally { els.analyze.disabled=false; els.analyze.innerHTML='Run freshness inspection <span>-></span>'; } });
function showResult(data) { els.resultEmpty.classList.add('hidden'); els.resultCard.classList.remove('hidden'); els.resultImage.src=data.image_url; els.resultLabel.textContent=data.label; els.resultDetail.textContent=data.model_status; els.resultConfidence.textContent=`${data.confidence}%`; els.visualScore.textContent=`${data.visual_score}/100`; }
async function refreshHistory(){ try { const response=await fetch('/api/inspections'); const data=await readApi(response); if(!response.ok) throw new Error(data.error || 'History is unavailable.'); if(!data.items.length){els.history.innerHTML='<p class="history-empty">No inspections yet.</p>';return;} els.history.innerHTML=data.items.map(item=>`<article class="history-item"><img src="/uploads/${encodeURIComponent(item.image_name)}" alt="Inspection ${item.id}"><b>${item.result_label}</b><p>${item.confidence}%</p><p>${new Date(item.created_at).toLocaleString()}</p></article>`).join(''); } catch { els.history.innerHTML='<p class="history-empty">Could not load inspection history.</p>'; }}
document.querySelector('#refreshHistory').addEventListener('click', refreshHistory);
refreshHistory();
