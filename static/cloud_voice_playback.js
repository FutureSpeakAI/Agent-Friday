/* Client-side playback rule for cloud voice audio.
 *
 * "Friday will not archive it" and "it is not cached
 * anywhere" are different promises, and only the second is what NON-DURABLE
 * should mean. The server sends `Cache-Control: no-store` and
 * `X-Friday-Voice-Durable: 0` for non-durable audio; this is the other half.
 *
 * The rule for non-durable audio:
 *   - never write it to a Blob URL that outlives playback
 *   - never hand it to the Cache API, IndexedDB, localStorage or a download
 *   - revoke the object URL as soon as playback ends or errors
 *   - never set `src` to the endpoint directly (that lets the browser's media
 *     cache hold a copy Friday never decided to keep and cannot enumerate to
 *     delete)
 *
 * WIRING: `ui_parts/app.html` and `index.html` both call /api/voice/tts
 * directly. Those two files were under concurrent edit when this shipped, so
 * this module is deliberately NOT imported there yet. Wiring it is one script
 * tag plus swapping the fetch call for `speakCloud()`. Until that happens the
 * server-side `no-store` is the only enforcement, which stops the HTTP cache
 * but not a UI that chooses to stash the bytes itself.
 */
(function (global) {
  "use strict";

  var ENDPOINT = "/api/voice/cloud/tts";

  /**
   * Synthesize and play one utterance through the selected cloud provider.
   *
   * Resolves with the served provider/model/cost so the caller can render the
   * indicator FROM THE SERVED PATH, never from what it asked for.
   *
   * On refusal (HTTP 503) it resolves with {ok:false, offer, code, message} and
   * DOES NOT play anything. The caller shows the offer and waits for the user.
   * Substituting local audio here would be the silent fallback the design
   * forbids, and would make the indicator lie.
   */
  function speakCloud(text, opts) {
    opts = opts || {};
    return fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // `no-store` on the request too: belt and braces, and it makes the
      // intent explicit at the call site rather than only in a response header.
      cache: "no-store",
      body: JSON.stringify({ text: text, provider: opts.provider || undefined })
    }).then(function (resp) {
      if (resp.status === 503) {
        return resp.json().then(function (body) {
          return {
            ok: false,
            surfaced: true,
            substituted: false,
            code: body.code,
            message: body.message,
            offer: body.offer,
            requested: body.requested
          };
        });
      }
      if (!resp.ok) {
        return resp.json().catch(function () { return {}; })
          .then(function (body) {
            return { ok: false, code: body.code || "error",
                     message: body.message || ("HTTP " + resp.status) };
          });
      }
      var durable = resp.headers.get("X-Friday-Voice-Durable") === "1";
      var served = {
        ok: true,
        provider: resp.headers.get("X-Friday-Voice-Provider"),
        model: resp.headers.get("X-Friday-Voice-Model"),
        chars: parseInt(resp.headers.get("X-Friday-Voice-Chars") || "0", 10),
        cost: resp.headers.get("X-Friday-Voice-Cost-USD"),
        durable: durable
      };
      return resp.blob().then(function (blob) {
        return playEphemeral(blob, durable).then(function () { return served; });
      });
    });
  }

  /**
   * Play a blob and drop every reference to it when playback finishes.
   *
   * `durable=false` is the strict path: the object URL is revoked on end, on
   * error, and on pause, and the blob is not retained anywhere afterwards. The
   * audio element is detached so a later `currentSrc` read cannot resurrect it.
   */
  function playEphemeral(blob, durable) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(blob);
      var audio = new Audio();
      var done = false;

      function cleanup(fn, arg) {
        if (done) return;
        done = true;
        try { audio.pause(); } catch (e) { /* already stopped */ }
        try { URL.revokeObjectURL(url); } catch (e) { /* already revoked */ }
        if (!durable) {
          // Detach so nothing can read the source back off the element.
          try { audio.removeAttribute("src"); audio.load(); } catch (e) {}
        }
        audio = null;
        blob = null;
        fn(arg);
      }

      audio.addEventListener("ended", function () { cleanup(resolve); });
      audio.addEventListener("error", function () {
        cleanup(reject, new Error("cloud voice playback failed"));
      });
      audio.src = url;
      audio.play().catch(function (e) { cleanup(reject, e); });
    });
  }

  /** Guard for callers tempted to persist. Throws rather than warns. */
  function assertMayPersist(served) {
    if (!served || served.durable !== true) {
      throw new Error(
        "this audio is non-durable and must not be saved, cached or " +
        "downloaded (Q3: the provider's terms require deletion on " +
        "termination, so a copy Friday keeps is a copy it cannot honour)");
    }
    return true;
  }

  global.FridayCloudVoice = {
    speakCloud: speakCloud,
    playEphemeral: playEphemeral,
    assertMayPersist: assertMayPersist
  };
})(typeof window !== "undefined" ? window : this);
