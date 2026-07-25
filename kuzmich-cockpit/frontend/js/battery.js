'use strict';
// Общий модуль батареи: poll /api/battery каждые 5с, обновляет #batt в хедере.
(async function(){
  function $(s){return document.querySelector(s)}
  async function pollBattery(){
    try{
      const r=await fetch('/api/battery');const d=await r.json();
      const el=$('#batt');if(!el)return;
      if(d.soc===null||d.soc===undefined){el.textContent='⚡ --%';el.className='badge batt';return;}
      el.textContent='⚡ '+d.soc+'%';
      el.className='badge batt'+(d.soc>50?' high':d.soc>20?' mid':' low');
    }catch(e){}
  }
  pollBattery();
  setInterval(pollBattery,5000);
})();
