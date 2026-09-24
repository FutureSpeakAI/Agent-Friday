"""Streamed speech must be played by one continuous node, never one per chunk.

Scheduling an AudioBufferSourceNode per arriving chunk is behind two separate
defects, and only one of them is a browser bug:

* Chrome 152 sometimes replaced the start of a chunk with a single 128-sample
  render block repeated about a hundred times — the dial-up screech reported
  against Gemini Live. Fixed in Chrome 153.
* Every chunk boundary is also a resampler reset and a scheduling rounding
  error. That one no browser version fixes. Measured here on Chrome 153 with a
  pure 440 Hz sine, the per-chunk path stepped the waveform 34 times in six
  seconds with the context pinned to 24 kHz and 38 times unpinned, by as much
  as 0.62 where the signal itself can only move 0.035 between samples.

So the check is not "is this browser buggy" — that would pass or fail with
whatever Chrome happens to be installed. It is "does what we render actually
follow the waveform we were given", which is true or false regardless.

Both detectors are proved able to fire against deliberately damaged audio
first, so a clean verdict means something.
"""
import functools
import http.server
import json
import pathlib
import re
import socketserver
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"
LIVE = REPO / "static" / "live" / "friday_live.html"
# index.html is the served UI; app.html is its hand-maintained mirror, and a
# mirror that still schedules per chunk is a fix waiting to be undone by the
# next person who regenerates from it. This test file originally checked only
# the two served pages and let the mirror go stale for exactly that reason.
APP = REPO / "ui_parts" / "app.html"
WORKLET = REPO / "static" / "js" / "friday_pcm_player.worklet.js"
WORKLET_URL = "/static/js/friday_pcm_player.worklet.js"
PAGE_PATH = "/__voice_playback_fixture.html"

STREAMING_PAGES = (INDEX, LIVE, APP)


def test_the_shared_player_exists_and_registers_its_processor():
    assert WORKLET.exists(), "%s is gone; both voice paths load it by URL" % WORKLET
    src = WORKLET.read_text(encoding="utf-8")
    assert "registerProcessor('friday-pcm-player'" in src
    assert "'flush'" in src, "the player must be able to drop a buffered turn on barge-in"


@pytest.mark.parametrize("page", STREAMING_PAGES, ids=lambda p: p.name)
def test_no_streaming_path_schedules_a_node_per_chunk(page):
    src = page.read_text(encoding="utf-8")
    offenders = [ln for ln in ("createBufferSource", "createBuffer(")
                 if ln in src]
    assert not offenders, (
        "%s builds audio buffers per chunk again (%s). One node per chunk steps "
        "the waveform at every boundary; feed the shared ring-buffer player "
        "instead." % (page.name, ", ".join(offenders))
    )


@pytest.mark.parametrize("page", STREAMING_PAGES, ids=lambda p: p.name)
def test_every_streaming_page_loads_the_one_shared_player(page):
    src = page.read_text(encoding="utf-8")
    assert WORKLET_URL in src, (
        "%s no longer loads %s, so the two voice paths can drift apart"
        % (page.name, WORKLET_URL)
    )


# ─── the behavioural half: drive a real browser and measure what it rendered ──

FIXTURE = """<!doctype html><meta charset=utf-8><body><script>
const SRC_RATE = 24000, FREQ = 440, AMP = 0.61;
const CAPTURE_SRC = `
class Cap extends AudioWorkletProcessor {
  constructor(){ super(); this.buf=new Float32Array(sampleRate*40); this.n=0;
    this.port.onmessage=()=>{ this.port.postMessage(this.buf.slice(0,this.n)); }; }
  process(inputs){ const i=inputs[0]&&inputs[0][0];
    if(i){ for(let k=0;k<i.length&&this.n<this.buf.length;k++) this.buf[this.n++]=i[k]; }
    return true; }
}
registerProcessor('cap', Cap);
`;
function sine(n){ const a=new Int16Array(n);
  for(let i=0;i<n;i++) a[i]=Math.round(Math.sin(2*Math.PI*FREQ*i/SRC_RATE)*AMP*32767); return a; }

// repeatRun: the Chrome 152 signature. discontinuities: steps the signal cannot take.
function analyse(s, rate){
  let run=0, best=0;
  for(let n=128;n<s.length;n++){
    if(Math.abs(s[n]-s[n-128])<1e-6 && Math.abs(s[n])>1e-4){ run++; if(run>best) best=run; }
    else run=0;
  }
  const maxStep = 2*Math.PI*FREQ/rate*AMP, limit = maxStep*2.5;
  let jumps=0, worst=0;
  for(let n=1;n<s.length;n++){
    const d=Math.abs(s[n]-s[n-1]);
    if(d>worst) worst=d;
    if(d>limit) jumps++;
  }
  return {repeatRun:best, discontinuities:jumps, worstStep:+worst.toFixed(4),
          allowedStep:+maxStep.toFixed(4), captured:s.length};
}

async function mk(pin){
  const ctx = pin ? new AudioContext({sampleRate:SRC_RATE}) : new AudioContext();
  if(ctx.state==='suspended') await ctx.resume();
  await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([CAPTURE_SRC],{type:'application/javascript'})));
  const cap=new AudioWorkletNode(ctx,'cap',{numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[1]});
  const g=ctx.createGain(); g.gain.value=0; cap.connect(g); g.connect(ctx.destination);
  return {ctx,cap};
}
const read = cap => new Promise(r=>{ cap.port.onmessage=e=>r(e.data); cap.port.postMessage('x'); });

// Loads the SHIPPED worklet file by its real URL, the way both pages do.
window.playThroughShippedPlayer = async (secs, chunk, pin) => {
  const {ctx,cap} = await mk(pin);
  await ctx.audioWorklet.addModule(WORKLET_URL);
  const node=new AudioWorkletNode(ctx,'friday-pcm-player',
    {numberOfInputs:0,numberOfOutputs:1,outputChannelCount:[1],processorOptions:{srcRate:SRC_RATE}});
  node.connect(cap);
  const total=SRC_RATE*secs, pcm=sine(total);
  for(let off=0;off<total;off+=chunk){
    const n=Math.min(chunk,total-off); const f=new Float32Array(n);
    for(let i=0;i<n;i++) f[i]=pcm[off+i]/32768;
    node.port.postMessage({type:'samples',data:f},[f.buffer]);
    await new Promise(r=>setTimeout(r,(n/SRC_RATE)*1000*0.6));   // faster than realtime, as Gemini does
  }
  await new Promise(r=>setTimeout(r,2000));
  const out=await read(cap); const rate=ctx.sampleRate; await ctx.close();
  return Object.assign({ctxRate:rate}, analyse(out,rate));
};

window.selftest = () => {
  const n=SRC_RATE*3, pcm=sine(n), f=new Float32Array(n);
  for(let i=0;i<n;i++) f[i]=pcm[i]/32768;
  const rep=f.slice(), st=10000;
  for(let b=1;b<100;b++) for(let i=0;i<128;i++) rep[st+b*128+i]=rep[st+i];
  const cut=f.slice();
  for(let k=1;k<30;k++) cut[5000*k] = -cut[5000*k];
  return {clean:analyse(f,SRC_RATE), repeatedBlocks:analyse(rep,SRC_RATE),
          stepped:analyse(cut,SRC_RATE)};
};
</script></body>"""


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the repo, so /static/js/... is fetched exactly as the app does."""

    def do_GET(self):                                          # noqa: N802
        if self.path.split("?")[0] == PAGE_PATH:
            body = FIXTURE.replace("WORKLET_URL", json.dumps(WORKLET_URL)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def audio_page():
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="playwright is not installed")
    server = socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d%s" % (server.server_address[1], PAGE_PATH)
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(
                    args=["--autoplay-policy=no-user-gesture-required"])
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            page = browser.new_page()
            page.goto(url)
            yield page
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def test_the_detectors_fire_on_damaged_audio(audio_page):
    """Without this, a clean verdict below would be unfalsifiable."""
    st = audio_page.evaluate("selftest()")
    assert st["clean"]["repeatRun"] <= 2 and st["clean"]["discontinuities"] == 0, (
        "the detectors flag undamaged audio: %r" % st["clean"])
    assert st["repeatedBlocks"]["repeatRun"] > 5000, (
        "the Chrome-152 detector missed a block repeated 100 times: %r"
        % st["repeatedBlocks"])
    assert st["stepped"]["discontinuities"] >= 15, (
        "the discontinuity detector missed 30 hard steps: %r" % st["stepped"])


@pytest.mark.parametrize("pin", [True, False],
                         ids=["ctx pinned to 24k", "ctx at device rate"])
def test_the_shipped_player_renders_the_waveform_it_was_given(audio_page, pin):
    r = audio_page.evaluate("playThroughShippedPlayer(5, 6720, %s)"
                            % ("true" if pin else "false"))
    assert r["captured"] > 40000, "captured almost nothing: %r" % r
    assert r["discontinuities"] == 0, (
        "the player stepped the waveform %d times (worst %.4f, the signal can "
        "only move %.4f). That is the grain that builds over a long reply: %r"
        % (r["discontinuities"], r["worstStep"], r["allowedStep"], r))
    assert r["repeatRun"] < 500, (
        "a render block is being repeated — the Chrome 152 screech: %r" % r)
