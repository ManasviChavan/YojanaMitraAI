from __future__ import annotations

import json


def speech_controls_html(text: str, language_code: str, height: int = 90) -> str:
    """Return a self-contained browser TTS widget with voice availability and playback controls."""
    safe_text = json.dumps(text, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    safe_language = json.dumps(language_code or "en-IN")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
    body{{font-family:system-ui,sans-serif;margin:0;color:#17345f}}.row{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
    button,select{{border:1px solid #cbd8e8;border-radius:9px;padding:8px 11px;background:#fff;color:#17345f}}
    button:disabled,select:disabled{{opacity:.45}}#status{{font-size:12px;color:#526985;margin-top:6px}}
    </style></head><body><div class="row"><button id="play">Play</button><button id="pause" disabled>Pause</button><button id="replay">Replay</button><button id="stop">Stop</button><select id="voice" aria-label="Available speech voice"></select></div><div id="status" role="status">Checking browser speech support…</div>
    <script>
    const text={safe_text}; const targetLang={safe_language};
    const synth=window.speechSynthesis; const play=document.getElementById('play');
    const pause=document.getElementById('pause'); const replay=document.getElementById('replay');
    const stop=document.getElementById('stop'); const picker=document.getElementById('voice');
    const status=document.getElementById('status'); let voices=[]; let utterance=null;
    function loadVoices(){{
      if(!synth){{status.textContent='Speech synthesis is not supported in this browser. Read the answer above.'; play.disabled=true; replay.disabled=true; picker.disabled=true; return;}}
      voices=synth.getVoices(); const lang=targetLang.toLowerCase().split('-')[0];
      const matching=voices.filter(v=>v.lang.toLowerCase().startsWith(lang));
      picker.replaceChildren();
      for(const voice of matching){{const opt=document.createElement('option');opt.value=voice.name;opt.textContent=voice.name+' ('+voice.lang+')';picker.appendChild(opt);}}
      const selected=matching.find(v=>v.lang.toLowerCase()===targetLang.toLowerCase())||matching[0];
      if(selected) picker.value=selected.name;
      const supported=matching.length>0;
      play.disabled=!supported; replay.disabled=!supported; picker.disabled=!supported;
      status.textContent=supported?'Voice available for '+targetLang+'.':'No browser voice found for '+targetLang+'. Try another language or use the text answer.';
    }}
    function speak(){{
      if(!synth||!voices.length) return;
      const lang=targetLang.toLowerCase().split('-')[0];
      const voice=voices.find(v=>v.name===picker.value&&v.lang.toLowerCase().startsWith(lang));
      if(!voice){{loadVoices();return;}}
      synth.cancel(); utterance=new SpeechSynthesisUtterance(text); utterance.voice=voice; utterance.lang=voice.lang;
      utterance.onstart=()=>pause.disabled=false; utterance.onend=()=>pause.disabled=true; utterance.onerror=()=>{{status.textContent='Playback failed. The text answer is still available.';pause.disabled=true;}};
      synth.speak(utterance);
    }}
    play.onclick=()=>{{if(synth.paused) synth.resume(); else speak();}};
    replay.onclick=speak; stop.onclick=()=>{{if(synth)synth.cancel();pause.disabled=true;}};
    pause.onclick=()=>{{if(!synth)return;if(synth.paused){{synth.resume();pause.textContent='Pause';}}else{{synth.pause();pause.textContent='Resume';}}}};
    picker.onchange=()=>{{if(synth)synth.cancel();pause.disabled=true;}};
    loadVoices(); if(synth)synth.onvoiceschanged=loadVoices;
    </script></body></html>"""
