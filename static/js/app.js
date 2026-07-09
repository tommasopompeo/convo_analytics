// Minimal progressive enhancement: transcription-status polling + copy button.
// No framework, no build step.

(function () {
  "use strict";

  // ── Transcription status polling (Transcribing screen) ─────────────────
  const poll = document.getElementById("transcribe");
  if (poll) {
    const fileId = poll.dataset.fileId;
    const spinner = document.getElementById("spinner");
    const statusLabel = document.getElementById("status-label");
    const errorBox = document.getElementById("status-error");
    const retry = document.getElementById("retry");

    const tick = async function () {
      try {
        const res = await fetch(`/files/${fileId}/status`, { cache: "no-store" });
        const data = await res.json();

        if (data.status === "done" && data.transcript_id) {
          window.location.href = `/transcripts/${data.transcript_id}/speakers`;
          return;
        }
        if (data.status === "failed") {
          if (spinner) spinner.style.display = "none";
          if (statusLabel) statusLabel.textContent = "Transcription didn't complete.";
          if (errorBox) {
            errorBox.textContent = data.error || "An unknown error occurred.";
            errorBox.style.display = "block";
          }
          if (retry) retry.style.display = "inline-flex";
          return;
        }
        // still uploaded / transcribing → keep polling
        setTimeout(tick, 2500);
      } catch (e) {
        setTimeout(tick, 4000);
      }
    };
    setTimeout(tick, 1200);
  }

  // ── Speaker chooser: live "chosen" highlight (Voices screen) ───────────
  const ownerRadios = document.querySelectorAll('input[name="owner"]');
  if (ownerRadios.length) {
    const sync = function () {
      ownerRadios.forEach(function (radio) {
        const card = radio.closest(".speaker-card");
        if (card) card.classList.toggle("chosen", radio.checked);
      });
    };
    ownerRadios.forEach(function (radio) {
      radio.addEventListener("change", sync);
    });
    sync();
  }

  // ── Copy-to-clipboard (Analyze screen) ─────────────────────────────────
  const copyBtn = document.getElementById("copy-prompt");
  if (copyBtn) {
    copyBtn.addEventListener("click", async function () {
      const target = document.getElementById(copyBtn.dataset.target);
      if (!target) return;
      const text = target.textContent || "";
      const original = copyBtn.textContent;
      try {
        await navigator.clipboard.writeText(text);
        copyBtn.textContent = "Copied ✓";
      } catch (e) {
        // Fallback: select the text so the user can copy manually.
        const range = document.createRange();
        range.selectNodeContents(target);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        copyBtn.textContent = "Press Ctrl+C to copy";
      }
      setTimeout(function () { copyBtn.textContent = original; }, 2000);
    });
  }
})();
