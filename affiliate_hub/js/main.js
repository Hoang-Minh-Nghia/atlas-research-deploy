/* =============================================================================
   ATLAS RESEARCH — Affiliate Hub script
   File đích: /var/www/atlasresearch.blog/affiliate_hub/js/main.js
   Trang vẫn hoạt động đầy đủ khi tắt JavaScript; script chỉ bổ sung tiện ích.
   ============================================================================= */
(function () {
  'use strict';

  // ---- Năm hiện tại ở footer ----
  var yearEl = document.querySelector('[data-year]');
  if (yearEl) yearEl.textContent = String(new Date().getFullYear());

  // ---- Nút "Sao chép" tên miền chính thức ----
  // Clipboard API có thể bị chặn (trình duyệt nhúng của Zalo/Facebook, iframe...)
  // nên luôn có phương án dự phòng execCommand.
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).catch(function () {
        return legacyCopy(text);
      });
    }
    return legacyCopy(text);
  }

  function legacyCopy(text) {
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy') ? resolve() : reject(new Error('copy failed'));
      } catch (err) {
        reject(err);
      }
      document.body.removeChild(ta);
    });
  }

  function flash(btn, message, ok) {
    var label = btn.querySelector('[data-label]');
    if (!label) return;
    if (!btn.dataset.original) btn.dataset.original = label.textContent;
    label.textContent = message;
    btn.classList.toggle('is-done', ok);
    clearTimeout(btn._timer);
    btn._timer = setTimeout(function () {
      label.textContent = btn.dataset.original;
      btn.classList.remove('is-done');
    }, 1800);
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-copy]'), function (btn) {
    btn.hidden = false;
    btn.addEventListener('click', function () {
      copyText(btn.getAttribute('data-copy')).then(
        function () { flash(btn, 'Đã sao chép', true); },
        function () { flash(btn, 'Không thể sao chép', false); }
      );
    });
  });

  // ---- Đo lượt click sang sàn đối tác (khi site đã gắn Google Analytics / GTM) ----
  document.addEventListener('click', function (e) {
    var link = e.target.closest ? e.target.closest('a[data-partner]') : null;
    if (!link) return;
    var detail = {
      partner: link.getAttribute('data-partner'),
      placement: link.getAttribute('data-placement') || ''
    };
    if (typeof window.gtag === 'function') {
      window.gtag('event', 'partner_click', detail);
    } else if (Array.isArray(window.dataLayer)) {
      window.dataLayer.push({ event: 'partner_click', partner: detail.partner, placement: detail.placement });
    }
  });
})();
