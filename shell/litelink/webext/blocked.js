// "Go back" on the blocked-site page (inline scripts aren't allowed in extension pages).
document.getElementById('back').addEventListener('click', () => {
  if (history.length > 1) history.back();
  else location.href = (self.LITELINK && self.LITELINK.home) || 'about:blank';
});
