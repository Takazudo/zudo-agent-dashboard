import {EditorState, Compartment} from '@codemirror/state';
import {EditorView, keymap, drawSelection, highlightActiveLine, lineNumbers, placeholder} from '@codemirror/view';
import {defaultKeymap, history, historyKeymap, undo, redo, undoDepth} from '@codemirror/commands';
import {vim, getCM, Vim} from '@replit/codemirror-vim';
// One actual EditorView. Expanding and themes only reconfigure it; no cloned textarea.
window.createComposerEditor=(host,callbacks={})=>{
 const modes=new Compartment(),appearance=new Compartment(),wrapping=new Compartment(),numbers=new Compartment(),editable=new Compartment(),label=new Compartment();
 let prefs=window.ThemeSettings.get(),enabled=false,paneId='',pendingPrefs=null,destroyed=false,restoreFrame=0,statusTimer=0;
 // The pinned Vim core has global registers/search/macro state. Fail closed if its
 // reset hook disappears; a session boundary must not retain that state.
 if(typeof Vim.resetVimGlobalState_!=='function')throw new Error('Vim state reset unavailable');
 const clearVimState=()=>Vim.resetVimGlobalState_();
 const nonce=document.querySelector('meta[name="csp-nonce"]')?.content;
 const states=new Map(),scrollPositions=new Map();
 const theme=()=>EditorView.theme({'&':{height:'100%',fontSize:prefs.fontSize+'px',color:'var(--text)',backgroundColor:'var(--editor-bg)'},'.cm-scroller':{fontFamily:'ui-monospace,SFMono-Regular,Consolas,monospace',overflow:'auto',lineHeight:'1.6'},'.cm-content':{padding:'10px 0',caretColor:'var(--text)'},'.cm-line':{padding:'0 12px'},'.cm-gutters':{backgroundColor:'var(--surface)',color:'var(--muted)',borderColor:'var(--border)'},'.cm-activeLine,.cm-activeLineGutter':{backgroundColor:'var(--hover)'},'&.cm-focused':{outline:'none'},'&.cm-focused .cm-selectionBackground,.cm-selectionBackground,::selection':{backgroundColor:'var(--selection)'},'.cm-cursor,.cm-dropCursor':{borderLeftColor:'var(--text)'},'.cm-fat-cursor':{background:'var(--text)',color:'var(--editor-bg)'},'.cm-panels':{backgroundColor:'var(--surface)',color:'var(--text)'},'.cm-panel input':{color:'var(--text)',background:'var(--editor-bg)'}},{dark:window.ThemeSettings.effective()==='dark'});
 const status=()=>{if(destroyed)return;const cm=getCM(view),state=cm?.state?.vim;const mode=!prefs.vim?'Text':state?.insertMode?'INSERT':state?.visualMode?'VISUAL':'NORMAL';const indicator=document.getElementById('editor-mode');if(indicator){indicator.textContent=prefs.vim?'Vim · '+mode:'CodeMirror';indicator.dataset.mode=mode.toLowerCase()}};
 const extensions=()=>[...(nonce?[EditorView.cspNonce.of(nonce)]:[]),modes.of(prefs.vim?vim({status:true}):[]),history(),drawSelection(),highlightActiveLine(),keymap.of([...defaultKeymap,...historyKeymap]),wrapping.of(prefs.wrap?EditorView.lineWrapping:[]),numbers.of(prefs.lineNumbers?lineNumbers():[]),appearance.of(theme()),editable.of([EditorState.readOnly.of(!enabled),EditorView.editable.of(enabled)]),label.of(EditorView.contentAttributes.of({'aria-label':'Compose draft. Enter writes a new line; use Send to submit.','spellcheck':'false'})),placeholder('Write an instruction…'),EditorView.updateListener.of(update=>{if(destroyed)return;if(update.docChanged||update.selectionSet)callbacks.change?.();status()}),EditorView.domEventHandlers({compositionstart:()=>{if(!destroyed)callbacks.composition?.(true);return false},compositionend:()=>{if(destroyed)return false;callbacks.composition?.(false);if(pendingPrefs){const next=pendingPrefs;pendingPrefs=null;statusTimer=setTimeout(()=>configure(next),0)}return false},keydown:event=>{if(event.key==='Escape'&&prefs.vim){event.stopPropagation()}return false}})];
 const view=new EditorView({state:EditorState.create({doc:'',extensions:extensions()}),parent:host});
 // Bubble only: Vim's own handler sees Escape first. Normal-mode Escape never escapes the editor.
 const onKeydown=event=>{if(event.key==='Escape'&&(prefs.vim||document.body.classList.contains('composer-expanded')))event.stopPropagation();clearTimeout(statusTimer);statusTimer=setTimeout(status,0)};
 view.dom.addEventListener('keydown',onKeydown);
 function configure(next,force=false){if(destroyed)return;if(view.composing){pendingPrefs=next;return}const prior=prefs;prefs=next;const top=view.scrollDOM.scrollTop,left=view.scrollDOM.scrollLeft;view.dispatch({effects:[...((force||prior.vim!==prefs.vim)?[modes.reconfigure(prefs.vim?vim({status:true}):[])]:[]),appearance.reconfigure(theme()),wrapping.reconfigure(prefs.wrap?EditorView.lineWrapping:[]),numbers.reconfigure(prefs.lineNumbers?lineNumbers():[])]});view.scrollDOM.scrollTop=top;view.scrollDOM.scrollLeft=left;status()}
 const onPreferences=event=>configure(event.detail);
 window.addEventListener('dashboard-preferences',onPreferences);
 const api={
  view,
  get value(){return view.state.doc.toString()},
  set value(text){if(destroyed||text===this.value)return;view.dispatch({changes:{from:0,to:view.state.doc.length,insert:String(text)}})},
  get selectionStart(){return view.state.selection.main.from},
  get selectionEnd(){return view.state.selection.main.to},
  get selectionDirection(){return view.state.selection.main.anchor>view.state.selection.main.head?'backward':'forward'},
  setSelectionRange(start,end,direction){if(destroyed)return;const max=view.state.doc.length;const a=Math.max(0,Math.min(max,start)),b=Math.max(0,Math.min(max,end));view.dispatch({selection:{anchor:direction==='backward'?b:a,head:direction==='backward'?a:b}})},
  setEnabled(next){if(destroyed)return;enabled=Boolean(next);view.dispatch({effects:editable.reconfigure([EditorState.readOnly.of(!enabled),EditorView.editable.of(enabled)])})},
  focus(){if(!destroyed&&enabled)view.focus()},
  hasFocus:()=>!destroyed&&view.hasFocus,
  get composing(){return !destroyed&&view.composing},
  setLabel(text){if(!destroyed)view.dispatch({effects:label.reconfigure(EditorView.contentAttributes.of({'aria-label':String(text),'spellcheck':'false'}))})},
  selectPane(id){
   if(destroyed||view.composing||typeof id!=='string'||!id||id===paneId)return false;
   if(paneId){states.set(paneId,view.state);scrollPositions.set(paneId,{top:view.scrollDOM.scrollTop,left:view.scrollDOM.scrollLeft})}
   const selection=host.ownerDocument.getSelection();
   if(selection&&(view.dom.contains(selection.anchorNode)||view.dom.contains(selection.focusNode)))selection.removeAllRanges();
   cancelAnimationFrame(restoreFrame);paneId=id;view.setState(states.get(id)||EditorState.create({doc:'',extensions:extensions()}));
   view.dispatch({effects:editable.reconfigure([EditorState.readOnly.of(!enabled),EditorView.editable.of(enabled)])});configure(prefs,true);
   const at=scrollPositions.get(id)||{top:0,left:0};view.scrollDOM.scrollTop=at.top;view.scrollDOM.scrollLeft=at.left;return true
  },
  measure(){if(!destroyed)view.requestMeasure()},
  snapshot:()=>destroyed?null:({top:view.scrollDOM.scrollTop,left:view.scrollDOM.scrollLeft,selection:view.state.selection,focused:view.hasFocus}),
  restore(snapshot){if(destroyed||!snapshot)return;if(snapshot.selection)view.dispatch({selection:snapshot.selection});view.requestMeasure();cancelAnimationFrame(restoreFrame);restoreFrame=requestAnimationFrame(()=>{if(destroyed)return;view.scrollDOM.scrollTop=snapshot.top;view.scrollDOM.scrollLeft=snapshot.left;if(snapshot.focused&&enabled&&!view.composing)view.focus()})},
  undo:()=>!destroyed&&undo(view),redo:()=>!destroyed&&redo(view),
  vimKey:key=>{if(destroyed)return;const cm=getCM(view);if(cm&&enabled&&prefs.vim){Vim.handleKey(cm,key);status()}},
  state:()=>destroyed?null:({pane:paneId,enabled,vim:prefs.vim,vimMode:!prefs.vim?'text':getCM(view)?.state?.vim?.insertMode?'insert':getCM(view)?.state?.vim?.visualMode?'visual':'normal',doc:view.state.doc.toString(),selection:view.state.selection.toJSON(),undoDepth:undoDepth(view.state),viewCount:host.querySelectorAll('.cm-editor').length}),
  // Call reset on disconnect, expiry, or session change. Only selectPane preserves drafts.
  reset(){if(destroyed)return;cancelAnimationFrame(restoreFrame);states.clear();scrollPositions.clear();pendingPrefs=null;paneId='';enabled=false;view.setState(EditorState.create({doc:'',extensions:extensions()}));clearVimState();status()},
  destroy(){if(destroyed)return;destroyed=true;enabled=false;pendingPrefs=null;states.clear();scrollPositions.clear();clearTimeout(statusTimer);cancelAnimationFrame(restoreFrame);window.removeEventListener('dashboard-preferences',onPreferences);view.dom.removeEventListener('keydown',onKeydown);view.destroy();clearVimState();if(window.composerEditor===api)delete window.composerEditor}
 };
 status();window.composerEditor=api;return api;
};
