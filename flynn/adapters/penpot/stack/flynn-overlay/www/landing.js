// flynn desk landing page: fills the install command from this site's origin and wires the copy button.
(function () {
  var cmd = document.getElementById('cmd');
  var btn = document.getElementById('copy');
  var label = document.getElementById('copy-label');
  var status = document.getElementById('copy-status');
  var text = 'curl -fsSL ' + location.origin + '/flynn/agent/install.sh | bash';
  cmd.textContent = text;

  function fallbackCopy() {
    var r = document.createRange();
    r.selectNodeContents(cmd);
    var s = window.getSelection();
    s.removeAllRanges(); s.addRange(r);
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) {}
    return ok;
  }
  var timer;
  function done(ok) {
    label.textContent = ok ? 'Copied' : 'Press Cmd+C';
    status.textContent = ok ? 'Install command copied.' : 'Copy failed. The command is selected, press Command C.';
    btn.classList.toggle('is-done', ok);
    clearTimeout(timer);
    timer = setTimeout(function () { label.textContent = 'Copy'; btn.classList.remove('is-done'); }, 2200);
  }
  btn.addEventListener('click', function () {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(fallbackCopy()); });
    } else {
      done(fallbackCopy());
    }
  });
})();
