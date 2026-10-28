"""Shared native-only popover controls for empty and populated widget pages."""
POPUP_UI = r'''<style>
body.native-popup{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
body.native-popup .hd{align-items:center;gap:6px}
body.native-popup .hd b{letter-spacing:0;color:var(--text);font-weight:600}
body.native-popup .hd .sub{display:none}
body.native-popup .hbtn,body.native-popup .windowbar button{min-width:26px;height:26px;border-radius:6px;font-size:13px;padding:0 5px;color:var(--text,#24343f);border:1px solid var(--pborder,#d9dfe5);background:var(--panel,#fff)}
body.native-popup #btnPin{width:auto;min-width:40px;font:600 11px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;white-space:nowrap}
body.native-popup #btnPin[aria-pressed="true"]{background:var(--accent,#007aff);color:white;border-color:transparent}
body.native-popup #btnPin:focus-visible,body.native-popup button:focus-visible,body.native-popup a:focus-visible{outline:2px solid var(--accent,#007aff);outline-offset:2px}
body.native-popup .hd,body.native-popup .windowbar{cursor:default}
body.native-popup[data-pinned="true"] .hd,body.native-popup[data-pinned="true"] .windowbar{cursor:grab}
body.native-popup:not([data-pinned="true"]) .rz{display:none}
body.native-popup[data-theme="wb"],body.native-popup:not([data-theme]){--bg:#f3f4f6;--panel:#fff;--pborder:#dfe3e8;--accent:#007aff;--accent2:#007aff;--bright:#1769aa;--text:#1d2530;--mid:#576474;--dim:#64748b;--faint:#788595;background:var(--bg);color:var(--text)}
body.native-popup[data-theme="wb"] .card,body.native-popup[data-theme="wb"] .model-block{border-radius:10px;animation:none}
body.native-popup[data-theme="wb"] .card::before,body.native-popup[data-theme="wb"] .card::after{display:none}
body.native-popup .fb,body.native-popup #loginBtn,body.native-popup #loginCancel,body.native-popup #retrySync{border:1px solid var(--pborder,#dfe3e8);border-radius:6px;padding:4px 8px;font:inherit;font-size:11px;background:var(--panel,#fff);color:var(--text,#24343f);cursor:pointer}
body.native-popup button:disabled{opacity:.5;cursor:default}
body.native-popup .windowbar{color:var(--text);padding:10px 14px}
body.native-popup .windowbar button{font:inherit;font-size:13px}
body.native-popup .diag{color:var(--dim);border-color:var(--pborder)}
body.native-popup .box{font-size:14px}
@media(prefers-color-scheme:dark){body.native-popup[data-theme="wb"],body.native-popup:not([data-theme]){--bg:#202124;--panel:#2b2d31;--pborder:#3a3d42;--accent:#4d9bff;--accent2:#4d9bff;--bright:#82b7ff;--text:#eef0f3;--mid:#b5beca;--dim:#9da9b8;--faint:#8592a2}}
</style><script>
(function(){
  let installed=false, pinned=false;
  function api(){return window.pywebview && window.pywebview.api;}
  function activity(kind,active){const a=api();if(a && a.popup_activity)a.popup_activity(kind,active).catch(()=>{});}
  function attach(){
    const a=api(), host=document.querySelector('.tbtns') || document.getElementById('windowControls');
    if(installed || !a || !a.popup_state || !host)return;
    installed=true;document.body.classList.add('native-popup');
    const button=document.createElement('button');button.id='btnPin';button.className='hbtn';button.type='button';
    host.prepend(button);
    function render(state){
      pinned=Boolean(state.pinned);document.body.dataset.pinned=String(pinned);
      button.textContent=pinned?'已固定':'Pin';button.setAttribute('aria-pressed',String(pinned));
      button.title=pinned?'取消固定，恢复自动收起':'固定面板；未固定时闲置 5 秒自动收起';
      button.setAttribute('aria-label',pinned?'取消固定面板':'固定面板');
    }
    render({pinned:false});a.popup_state().then(render).catch(()=>{});
    button.onclick=async()=>{button.disabled=true;try{render(await a.set_pinned(!pinned));}finally{button.disabled=false;}};
    const root=document.documentElement;
    root.addEventListener('pointerenter',()=>activity('pointer',true));
    root.addEventListener('pointerleave',()=>activity('pointer',false));
    document.addEventListener('pointerdown',()=>activity('pulse',false),{passive:true});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();a.minimize_widget();}else{activity('pulse',false);}});
    const editing=e=>Boolean(e && (e.matches('input,textarea,select') || e.isContentEditable));
    document.addEventListener('focusin',e=>activity('input',editing(e.target)));
    document.addEventListener('focusout',e=>activity('input',editing(e.relatedTarget)));
    window.addEventListener('blur',()=>{activity('pointer',false);activity('input',false);});
  }
  window.addEventListener('pywebviewready',attach);attach();
})();
</script>'''
