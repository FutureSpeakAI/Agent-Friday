/* Shared data and helpers for the Media prototypes. Not product code.
   One CARD per piece of work. The same cards feed the Library, the Pipeline board
   and the Calendar; the card's fields are the spec's record (media-workspace.md §4.2).
   Titles, names and dates are synthetic. */
(function () {
  var D = function (off, h) { var d = new Date(2026, 8, 30, h || 9, 0, 0); d.setDate(d.getDate() + off); return d; };
  window.STATUSES = [
    ['idea', 'Idea'], ['draft', 'Draft'], ['review', 'In review'], ['scheduled', 'Scheduled'], ['published', 'Published']
  ];
  window.KIND_WORD = { draft: 'Draft', article: 'Article', episode: 'Episode', imageset: 'Image set', image: 'Image', video: 'Video', page: 'Page', chart: 'Chart', doc: 'Document', deck: 'Deck', post: 'Post', code: 'Codebase', music: 'Music', audio: 'Audio', model3d: '3D' };
  window.KIND_GLYPH = { draft: 'doc', article: 'doc', episode: 'podcast', imageset: 'image', image: 'image', video: 'video', page: 'page', chart: 'chart', doc: 'doc', deck: 'office', post: 'post', code: 'code', music: 'music', audio: 'audio', model3d: 'model3d' };

  /* id, kind, title, status, when (the date that matters for the status), project,
     maker (model, on this PC?), sources, privacy, published (where), rel, text */
  window.CARDS = [
    { id: 'c01', kind: 'article',  title: 'The ferry story',                      status: 'review',    when: D(0, 8),  project: 'Harbour series', maker: 'Local 27B · this PC',        sources: ['Field notes', '3 interviews (wiki)'],       privacy: 'private',   published: null,                       signed: true,  words: 1420 },
    { id: 'c02', kind: 'episode',  title: 'Three charts, one morning',            status: 'draft',     when: D(0, 8),  project: 'Harbour series', maker: 'Local 27B · Kokoro · this PC', sources: ['Q3 subscriptions by channel', '+2 charts'],  privacy: 'private',   published: null,                       signed: true,  dur: '9:40' },
    { id: 'c03', kind: 'chart',    title: 'Q3 subscriptions by channel',          status: 'published', when: D(-1, 18), project: 'Harbour series', maker: 'Data mode · this PC',        sources: ['subs-q3.csv'],                              privacy: 'published', published: 'Showcase page · this PC', signed: true },
    { id: 'c04', kind: 'post',     title: '"Monday" · 3 platforms',               status: 'scheduled', when: D(1, 9),  project: 'Harbour series', maker: 'Composer · this PC',          sources: ['Harbour at blue hour (image set)'],        privacy: 'private',   published: null, targets: ['Bluesky', 'LinkedIn', 'Mastodon'], signed: true },
    { id: 'c05', kind: 'imageset', title: 'Harbour at blue hour, 4 takes',        status: 'published', when: D(-2, 10), project: 'Harbour series', maker: 'Local image · this PC',       sources: ['harbour-01', 'palette'],                    privacy: 'published', published: 'Bluesky · Mon',           signed: true,  n: 4 },
    { id: 'c06', kind: 'draft',    title: 'Reply to the harbour board',            status: 'draft',     when: D(0, 11), project: null,             maker: 'Local 27B · this PC',        sources: ['Mail thread (3)'],                          privacy: 'private',   published: null,                       signed: false, channel: 'Mail' },
    { id: 'c07', kind: 'deck',     title: 'Pitch deck v3',                        status: 'review',    when: D(0, 14), project: 'Harbour series', maker: 'Office engine · this PC',     sources: ['The ferry story'],                          privacy: 'private',   published: null,                       signed: true,  pages: 10 },
    { id: 'c08', kind: 'page',     title: 'Ferry story · showcase page',          status: 'published', when: D(-3, 18), project: 'Harbour series', maker: 'Showcase engine · this PC',   sources: ['The ferry story', 'Q3 subscriptions'],      privacy: 'published', published: 'This PC publish host',   signed: true },
    { id: 'c09', kind: 'idea',     title: 'A lighthouse keeper on the 06:40',     status: 'idea',      when: D(0, 7),  project: 'Harbour series', maker: 'You',                        sources: [],                                           privacy: 'private',   published: null,                       signed: false },
    { id: 'c10', kind: 'video',    title: 'Teaser (timeline, 0:42)',               status: 'scheduled', when: D(2, 9),  project: 'Harbour series', maker: 'FFmpeg · this PC',            sources: ['Harbour loop', 'Low tide', 'Read aloud'],   privacy: 'private',   published: null, targets: ['YouTube'],  signed: true,  dur: '0:42' },
    { id: 'c11', kind: 'code',     title: 'tide-table',                           status: 'draft',     when: D(-1, 16), project: 'Harbour series', maker: 'Salon · Claude seat',         sources: ['brief: a tide table'],                      privacy: 'private',   published: null,                       signed: true,  salon: true },
    { id: 'c12', kind: 'music',    title: 'Low tide (ambient, 2:10)',             status: 'published', when: D(-4, 12), project: null,             maker: 'Lyria · cloud, asked first',  sources: ['mood: low tide'],                           privacy: 'published', published: 'In "Teaser"',             signed: true,  dur: '2:10' },
    { id: 'c13', kind: 'draft',    title: 'Weekly note to subscribers',           status: 'idea',      when: D(3, 9),  project: 'Newsletter',     maker: 'You',                        sources: [],                                           privacy: 'private',   published: null,                       signed: false, channel: 'Newsletter' },
    { id: 'c14', kind: 'doc',      title: 'Filled: harbour permit.pdf',           status: 'published', when: D(-5, 15), project: null,             maker: 'PDF forms · this PC',         sources: ['permit template'],                          privacy: 'shared',    published: 'Sent by mail · Thu',      signed: false },
    { id: 'c15', kind: 'post',     title: 'Ferry quote card',                     status: 'review',    when: D(0, 16), project: 'Harbour series', maker: 'Composer · this PC',          sources: ['The ferry story'],                          privacy: 'private',   published: null, targets: ['Instagram'], signed: true, held: true },
    { id: 'c16', kind: 'audio',    title: 'Read aloud: the ferry story',          status: 'draft',     when: D(-1, 9),  project: 'Harbour series', maker: 'Kokoro · this PC',            sources: ['The ferry story'],                          privacy: 'private',   published: null,                       signed: false, dur: '4:12' },
    { id: 'c17', kind: 'article',  title: 'What the quay count tells you',        status: 'idea',      when: D(1, 9),  project: 'Harbour series', maker: 'You',                        sources: ['Q3 subscriptions by channel'],              privacy: 'private',   published: null,                       signed: false },
    { id: 'c18', kind: 'post',     title: '"The 06:40 left on time"',             status: 'published', when: D(-1, 8),  project: 'Harbour series', maker: 'Composer · this PC',          sources: ['The ferry story'],                          privacy: 'published', published: 'Bluesky, Mastodon · yesterday', signed: true, targets: ['Bluesky', 'Mastodon'] },
    { id: 'c19', kind: 'episode',  title: 'Field notes, talked through',          status: 'published', when: D(-6, 17), project: 'Harbour series', maker: 'Local 27B · Kokoro · this PC', sources: ['Field notes'],                            privacy: 'private',   published: 'Kept on this PC',         signed: true,  dur: '6:05' },
    { id: 'c20', kind: 'deck',     title: 'Harbour numbers · 6 slides',           status: 'scheduled', when: D(4, 10), project: 'Harbour series', maker: 'Office engine · this PC',     sources: ['Q3 subscriptions by channel'],              privacy: 'private',   published: null, targets: ['Board meeting'], signed: true, pages: 6 }
  ];
  CARDS.forEach(function (c) { if (c.kind === 'idea') c.kind = 'draft'; });

  window.fmtWhen = function (d) {
    var t = D(0, 0); var diff = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - new Date(t.getFullYear(), t.getMonth(), t.getDate())) / 86400000);
    var hm = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    if (diff === 0) return 'Today ' + hm; if (diff === 1) return 'Tomorrow ' + hm; if (diff === -1) return 'Yesterday';
    return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
  };
  window.statusPill = function (s, held) {
    if (held) return '<span class="pill needs-you">Held for you</span>';
    var m = { idea: 'dim', draft: 'working', review: 'needs-you', scheduled: 'neutral', published: 'ok' };
    var w = { idea: 'Idea', draft: 'Draft', review: 'In review', scheduled: 'Scheduled', published: 'Published' };
    return '<span class="pill ' + m[s] + '">' + w[s] + '</span>';
  };
  window.privacyPill = function (c) {
    if (c.privacy === 'published') return '<span class="pill public">Published</span>';
    if (c.privacy === 'shared') return '<span class="pill public">Shared</span>';
    return '<span class="pill private">Private</span>';
  };
  window.cardHTML = function (c, opts) {
    opts = opts || {};
    var g = KIND_GLYPH[c.kind], w = KIND_WORD[c.kind];
    var extra = c.dur ? '<span class="dur">' + c.dur + '</span>' : c.n ? '<span class="dur">' + c.n + ' takes</span>' : c.pages ? '<span class="dur">' + c.pages + ' pages</span>' : c.words ? '<span class="dur">' + c.words + ' words</span>' : '';
    return '<button class="item card-item" role="option" data-id="' + c.id + '" aria-selected="' + (opts.selected ? 'true' : 'false') + '">' +
      (opts.noThumb ? '' : '<div class="thumb">' + w + extra + '</div>') +
      '<div class="body"><div class="title">' + c.title + '</div>' +
      '<div class="meta">' + glyph(g, w) + '<span>·</span><span>' + fmtWhen(c.when) + '</span></div>' +
      '<div class="meta">' + statusPill(c.status, c.held) + privacyPill(c) + (c.signed ? '' : '<span class="pill neutral">Unsigned</span>') + '</div>' +
      (c.published ? '<div class="meta"><span class="muted">at</span> <span>' + c.published + '</span></div>' : (c.targets ? '<div class="meta"><span class="muted">to</span> <span>' + c.targets.join(', ') + '</span></div>' : '')) +
      '</div></button>';
  };
  window.cardById = function (id) { return CARDS.filter(function (c) { return c.id === id; })[0]; };

  /* The Media head row: the three views as one segment control, the default views beside. */
  window.mediaHead = function (view, counts) {
    var pub = CARDS.filter(function (c) { return c.status === 'published'; }).length;
    var prog = CARDS.filter(function (c) { return c.status === 'draft' || c.status === 'review'; }).length;
    return '<div class="st-head">' +
      '<h1>Media</h1>' +
      '<div class="seg" role="tablist" aria-label="Views">' +
      ['library', 'board', 'calendar'].map(function (v) { var L = { library: 'Library', board: 'Pipeline', calendar: 'Calendar' }[v]; return v === view ? '<button class="btn active" role="tab" aria-selected="true">' + L + '</button>' : '<a class="btn" role="tab" href="' + v + '.html">' + L + '</a>'; }).join('') +
      '</div>' +
      '<span class="small muted">' + (counts || (CARDS.length + ' pieces of work · ' + prog + ' in progress · ' + pub + ' published')) + '</span>' +
      '<span class="spacer" style="flex:1"></span>' +
      '<button class="btn sm" id="popout" title="Open Media in its own tab (the shell\'s one top bar comes along)">⧉ Own tab</button>' +
      '<a class="btn primary" href="card.html?new=1">+ New</a>' +
      '</div>';
  };
  document.addEventListener('DOMContentLoaded', function () {
    var p = document.getElementById('popout'); if (p) p.addEventListener('click', function () { window.open('popout.html', '_blank'); });
  });
})();
