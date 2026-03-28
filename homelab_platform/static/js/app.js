function setOutput(text){document.getElementById('operation-output').textContent=text;}
function setLog(text){document.getElementById('log-output').textContent=text;}
async function viewLog(appId){const res=await fetch(`/api/logs/${appId}`);const data=await res.json();setLog(data.content||data.message||JSON.stringify(data,null,2));}
async function refresh(){
  const res=await fetch('/api/bundles');
  const data=await res.json();
  const bundleList=document.getElementById('bundle-list');
  bundleList.innerHTML='';
  const installedMap=new Map((data.installed||[]).map(x=>[x.id,x]));

  (data.bundles||[]).forEach(b=>{
    const installed = installedMap.get(b.id);
    const div=document.createElement('div');
    div.className='card';
    const badgeText = b.installed ? 'installed' : (b.install_status || 'available');
    const status = `<span class="badge ${badgeText}">${badgeText}</span>`;
    div.innerHTML=`<strong>${b.display_name||b.name}</strong> ${b.version?`(${b.version})`:''} ${status}<br><small>${b.filename}</small>${b.port?`<br><small>Port: ${b.port}</small>`:''}${b.installed && b.installed_version?`<br><small>Installed: ${b.installed_version}</small>`:''}${b.last_error?`<br><small class="error">${b.last_error}</small>`:''}`;
    const btn=document.createElement('button');
    btn.textContent=b.installed?'Reinstall':'Install';
    btn.onclick=async()=>{
      setOutput(`${b.installed?'Reinstalling':'Installing'} ${b.filename}...`);
      const r=await fetch('/api/install',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bundle_filename:b.filename})});
      const result=await r.json();
      setOutput(result.message||JSON.stringify(result,null,2));
      await refresh();
    };
    div.appendChild(btn);
    if(installed?.log_path){const logBtn=document.createElement('button');logBtn.textContent='View Log';logBtn.onclick=()=>viewLog(installed.id);div.appendChild(logBtn);}
    bundleList.appendChild(div);
  });

  const installed=document.getElementById('installed-list');
  installed.innerHTML='';
  (data.installed||[]).filter(a=>a.is_installed || a.install_status==='failed' || a.install_status==='installing').forEach(a=>{
    const div=document.createElement('div');
    div.className='card';
    div.innerHTML=`<strong>${a.name}</strong> (${a.version||''}) <span class="badge ${a.install_status||'unknown'}">${a.install_status||'unknown'}</span><br><small>ID: ${a.id}</small>${a.port?`<br><small>Port: ${a.port}</small>`:''}${a.last_error?`<br><small class="error">${a.last_error}</small>`:''}`;
    if(a.is_installed){
      const btn=document.createElement('button');
      btn.textContent='Remove';
      btn.onclick=async()=>{
        setOutput(`Removing ${a.id}...`);
        const r=await fetch('/api/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({app_id:a.id})});
        const result=await r.json();
        setOutput(result.message||JSON.stringify(result,null,2));
        await refresh();
      };
      div.appendChild(btn);
    }
    if(a.log_path){const logBtn=document.createElement('button');logBtn.textContent='View Log';logBtn.onclick=()=>viewLog(a.id);div.appendChild(logBtn);}
    installed.appendChild(div);
  });
}
document.getElementById('upload-form').addEventListener('submit', async e=>{e.preventDefault();const fd=new FormData();fd.append('file',document.getElementById('bundle-file').files[0]);const res=await fetch('/api/upload',{method:'POST',body:fd});const result=await res.json();document.getElementById('upload-result').textContent=JSON.stringify(result,null,2);refresh();});refresh();
