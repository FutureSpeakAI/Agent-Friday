/* Shared prototype helpers for the Studio prototypes. Not product code.
   - protoNav(current): the prototype strip and the skip link.
   - shell(opts): draws the unified shell's top bar around the page (unified-shell.md 4),
     with the chat tray toggle (5) and the fullscreen-with-chat key, Ctrl+Shift+F.
   - toast(text): the one notice style (fridayToast in the product).
   - glyph(kind, word): an outline mark per medium, always beside its word.
   - triadDefs(): the gradient every outline glyph strokes with (BRAND.md, icons). */
(function () {
  window.protoNav = function protoNav(current) {
    var pages = [
      ['index.html', 'Map'],
      ['library.html', '1 Library'],
      ['create.html', '2 Create'],
      ['viewer.html', '3 Viewer'],
      ['actions.html', '4 Actions'],
      ['boundaries.html', '5 Studio and News'],
      ['shell.html', '6 In the shell']
    ];
    var bar = document.createElement('div');
    bar.className = 'proto-bar';
    bar.innerHTML = '<span class="tag">Prototype</span><span>Integrated Studio · design prototype, not product code</span>' +
      '<nav aria-label="Prototype screens">' + pages.map(function (p) {
        return '<a href="' + p[0] + '"' + (p[0] === current ? ' aria-current="page"' : '') + '>' + p[1] + '</a>';
      }).join('') + '</nav>';
    document.body.prepend(bar);
    var skip = document.createElement('a');
    skip.className = 'skip'; skip.href = '#main'; skip.textContent = 'Skip to content';
    document.body.prepend(skip);
  };

  window.triadDefs = function triadDefs() {
    if (document.getElementById('triad')) return;
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', '0'); svg.setAttribute('height', '0'); svg.style.position = 'absolute';
    svg.innerHTML = '<defs><linearGradient id="triad" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="#00d4ff"/><stop offset=".5" stop-color="#7b61ff"/><stop offset="1" stop-color="#ff00ff"/>' +
      '</linearGradient></defs>';
    document.body.prepend(svg);
  };

  /* Outline glyphs on a 32 grid, one per medium. The word always sits beside the glyph. */
  var GLYPHS = {
    image: '<rect x="4" y="6" width="24" height="20" rx="3"/><circle cx="11" cy="13" r="2.5"/><path d="M4 23l7-7 6 6 4-4 7 7"/>',
    video: '<rect x="4" y="7" width="18" height="18" rx="3"/><path d="M22 13l6-3v12l-6-3z"/>',
    podcast: '<rect x="12" y="4" width="8" height="14" rx="4"/><path d="M7 14a9 9 0 0 0 18 0M16 23v5M11 28h10"/>',
    audio: '<path d="M6 12v8h5l7 6V6l-7 6z"/><path d="M22 11a7 7 0 0 1 0 10M25 8a11 11 0 0 1 0 16"/>',
    music: '<path d="M12 24V8l14-3v16"/><circle cx="9" cy="24" r="3.5"/><circle cx="23" cy="21" r="3.5"/>',
    doc: '<path d="M9 4h10l7 7v17H9z"/><path d="M19 4v7h7M13 17h10M13 22h10"/>',
    office: '<rect x="5" y="7" width="22" height="18" rx="2"/><path d="M5 13h22M12 13v12M19 13v12"/>',
    chart: '<path d="M5 27h22"/><rect x="8" y="15" width="4" height="9"/><rect x="15" y="9" width="4" height="15"/><rect x="22" y="12" width="4" height="12"/>',
    model3d: '<path d="M16 4l11 6v12l-11 6-11-6V10z"/><path d="M5 10l11 6 11-6M16 16v12"/>',
    page: '<rect x="4" y="6" width="24" height="20" rx="3"/><path d="M4 12h24M9 9h.01M13 9h.01"/>',
    code: '<path d="M12 10l-6 6 6 6M20 10l6 6-6 6M18 7l-4 18"/>',
    post: '<path d="M6 8h20v12H13l-5 4v-4H6z"/><path d="M11 13h10M11 16h6"/>',
    show: '<circle cx="16" cy="16" r="11"/><circle cx="16" cy="16" r="3"/><path d="M16 5v4M16 23v4M5 16h4M23 16h4"/>'
  };
  window.glyph = function glyph(kind, word) {
    var g = GLYPHS[kind] || GLYPHS.doc;
    return '<span class="type"><svg viewBox="0 0 32 32" aria-hidden="true">' + g + '</svg>' + (word || '') + '</span>';
  };

  window.toast = function toast(text) {
    var t = document.querySelector('.toast');
    if (!t) { t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
    t.textContent = text; t.classList.add('show');
    clearTimeout(window.__toastT);
    window.__toastT = setTimeout(function () { t.classList.remove('show'); }, 2800);
  };

  /* The shell around a Studio page: the one top bar and the chat tray. */
  window.shell = function shell(opts) {
    opts = opts || {};
    var main = document.getElementById('main');
    var bar = document.createElement('header');
    bar.className = 'top-bar';
    bar.setAttribute('aria-label', 'Top bar');
    bar.innerHTML =
      '<a class="lockup" href="index.html" title="Back to the desktop"><span class="product">Agent Friday™</span><span class="by">by</span><span class="maker">FutureSpeak.AI™</span></a>' +
      '<span class="context">' + glyph('show', '') + '<span class="ws">' + (opts.title || 'Studio') + '</span>' + (opts.tools || '') + '</span>' +
      '<span class="spacer"></span>' +
      '<button class="chip" aria-haspopup="listbox" title="Model selector">Local 27B ▾</button>' +
      '<button class="chip" title="Notifications" aria-label="Notifications">○</button>' +
      '<button class="chip" id="chatToggle" aria-pressed="false" title="Chat tray">Chat</button>' +
      '<button class="chip" id="fsToggle" aria-pressed="false" title="Fullscreen with chat, Ctrl+Shift+F">⤢</button>' +
      '<button class="chip" title="Settings" aria-label="Settings">⚙</button>' +
      '<span class="light" title="Connected" aria-label="Connected"></span>';
    var wrap = document.createElement('div');
    wrap.className = 'shell' + (opts.chatOpen ? ' chat-open' : '');
    main.parentNode.insertBefore(bar, main);
    main.parentNode.insertBefore(wrap, main);
    wrap.appendChild(main);
    main.classList.add('ws-tab-body');
    var tray = document.createElement('aside');
    tray.className = 'chat-panel'; tray.setAttribute('aria-label', 'Chat tray');
    tray.innerHTML = '<div class="label">Chat · docked beside the workspace</div>' +
      '<div class="msg me">Make a podcast out of the three charts from this morning.</div>' +
      '<div class="msg">Queued as a Studio episode, "Three charts, one morning". It reads the computed numbers only; it goes into the Library when it is spoken.</div>' +
      '<input type="text" placeholder="Say or type…" aria-label="Message">';
    wrap.appendChild(tray);
    var chatBtn = bar.querySelector('#chatToggle'), fsBtn = bar.querySelector('#fsToggle');
    function sync() {
      var open = wrap.classList.contains('chat-open');
      chatBtn.setAttribute('aria-pressed', String(open)); chatBtn.classList.toggle('on', open);
      var fs = document.body.classList.contains('fr-fs-chat');
      fsBtn.setAttribute('aria-pressed', String(fs)); fsBtn.classList.toggle('on', fs);
    }
    chatBtn.addEventListener('click', function () { wrap.classList.toggle('chat-open'); sync(); });
    fsBtn.addEventListener('click', function () {
      document.body.classList.toggle('fr-fs-chat'); wrap.classList.add('chat-open'); sync();
      toast(document.body.classList.contains('fr-fs-chat') ? 'Fullscreen with chat. Saved for Studio.' : 'Back to the window.');
    });
    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'F' || e.key === 'f')) { e.preventDefault(); fsBtn.click(); }
    });
    sync();
  };

  document.addEventListener('DOMContentLoaded', function () { triadDefs(); });
})();
