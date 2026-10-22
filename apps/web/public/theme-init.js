/* v0.1.23 (B7): đặt data-theme trên <html> TRƯỚC khi React chạy để không nháy màu khi tải trang.
 * Tệp riêng (không inline) vì CSP chỉ cho script 'self'. Đọc cùng khoá localStorage với src/lib/uiStore.ts
 * (`theme` = lựa chọn gần nhất trên trình duyệt này; theo từng người dùng thì React chỉnh lại sau khi đăng nhập). */
(function () {
  var pref = 'system';
  try {
    var raw = window.localStorage.getItem('gh-ui');
    var st = raw ? JSON.parse(raw).state : null;
    if (st && (st.theme === 'light' || st.theme === 'dark')) pref = st.theme;
  } catch (e) {
    /* chế độ riêng tư / bị chặn lưu trữ → theo hệ thống */
  }
  var light =
    pref === 'light' ||
    (pref === 'system' && !!window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches);
  document.documentElement.setAttribute('data-theme', light ? 'light' : 'dark');
})();
