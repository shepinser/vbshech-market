/* Небольшие интерактивные детали. Формы и данные обрабатываются сервером. */
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const toastContainer = document.querySelector('.toasts');
function toast(message, error = false, href = null) {
  const element = document.createElement('div');
  element.className = `toast${error ? ' error' : ''}`;
  element.textContent = message;
  if (href) {
    const link = document.createElement('a');
    link.href = href;
    link.textContent = 'Посмотреть';
    element.append(link);
  }
  toastContainer.append(element);
  setTimeout(() => {
    element.classList.add('leaving');
    setTimeout(() => element.remove(), 250);
  }, 6000);
}

document.addEventListener('click', event => {
  const open = event.target.closest('[data-open]');
  if (open) {
    const dialog = document.getElementById(open.dataset.open);
    if (dialog) dialog.showModal();
  }
  const close = event.target.closest('[data-close]');
  if (close && close.closest('dialog').getAttribute('aria-busy') !== 'true') close.closest('dialog').close();
  const toggle = event.target.closest('[data-toggle]');
  if (toggle) {
    const expanded = document.getElementById(toggle.dataset.toggle).classList.toggle('is-open');
    toggle.setAttribute('aria-expanded', expanded);
  }
  const button = event.target.closest('.primary:not(:disabled)');
  if (button && !reducedMotion.matches) {
    const rect = button.getBoundingClientRect();
    const ripple = document.createElement('span');
    const size = Math.max(rect.width, rect.height);
    ripple.className = 'ripple';
    ripple.style.cssText = `width:${size}px;height:${size}px;left:${event.clientX - rect.left - size / 2}px;top:${event.clientY - rect.top - size / 2}px`;
    button.append(ripple);
    setTimeout(() => ripple.remove(), 600);
  }
});
document.querySelectorAll('dialog').forEach(dialog => {
  dialog.addEventListener('click', event => {
    if (event.target !== dialog || dialog.getAttribute('aria-busy') === 'true') return;
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  });
});

document.querySelector('[data-location-search]')?.addEventListener('input', event => {
  const value = event.target.value.trim().toLocaleLowerCase('ru');
  let visible = 0;
  document.querySelectorAll('[data-location-text]').forEach(row => {
    row.hidden = !row.dataset.locationText.includes(value);
    if (!row.hidden) visible++;
  });
  document.querySelector('.location-no-results').hidden = visible > 0;
});

// Названия уже загружены вместе со страницей: подсказки не ждут Access и сеть.
const search = document.querySelector('.search input');
const suggestions = document.getElementById('search-suggestions');
const searchIndex = JSON.parse(document.getElementById('search-index')?.textContent || '[]');
const translit = Object.fromEntries(Array.from('абвгдеёжзийклмнопрстуфхцчшщъыьэюя').map((char, index) =>
  [char, ['a','b','v','g','d','e','e','zh','z','i','i','k','l','m','n','o','p','r','s','t','u','f','kh','ts','ch','sh','shch','','y','','e','yu','ya'][index]]));
const searchKey = value => Array.from(value.toLocaleLowerCase('ru')).map(char => translit[char] ?? char).join('').trim();
function hideSuggestions() {
  if (!suggestions || !search) return;
  suggestions.hidden = true;
  search.setAttribute('aria-expanded', 'false');
}
function showSuggestions() {
  const value = searchKey(search.value);
  hideSuggestions();
  if (!value) return;
  const items = searchIndex.filter(item => item.key.includes(value)).slice(0, 6);
  suggestions.replaceChildren();
  for (const item of items) {
    const link = document.createElement('a');
    link.href = item.url;
    // Сопоставляем позиции транслитерации с исходным названием для подсветки.
    const positions = [];
    Array.from(item.title.toLocaleLowerCase('ru')).forEach((char, index) => {
      for (const unused of (translit[char] ?? char)) positions.push(index);
    });
    const start = item.key.indexOf(value);
    const from = positions[start];
    const to = positions[start + value.length - 1] + 1;
    const mark = document.createElement('mark');
    mark.textContent = item.title.slice(from, to);
    link.append(document.createTextNode(item.title.slice(0, from)), mark, document.createTextNode(item.title.slice(to)));
    suggestions.append(link);
  }
  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'search-empty';
    empty.textContent = 'Товары не найдены';
    suggestions.append(empty);
  }
  suggestions.hidden = false;
  search.setAttribute('aria-expanded', 'true');
}
search?.addEventListener('input', showSuggestions);
search?.addEventListener('focus', showSuggestions);

// Выделение разрешено только там, где пользователь вводит текст.
const editable = target => target instanceof Element && Boolean(target.closest('input, textarea, [contenteditable="true"]'));
document.addEventListener('selectstart', event => { if (!editable(event.target)) event.preventDefault(); });
document.addEventListener('dragstart', event => { if (!editable(event.target)) event.preventDefault(); });
search?.addEventListener('keydown', event => {
  if (event.key === 'Escape') hideSuggestions();
  if (event.key === 'ArrowDown' && !suggestions.hidden) {
    event.preventDefault();
    suggestions.querySelector('a')?.focus();
  }
});
suggestions?.addEventListener('keydown', event => {
  const links = Array.from(suggestions.querySelectorAll('a'));
  const index = links.indexOf(document.activeElement);
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    const next = index + (event.key === 'ArrowDown' ? 1 : -1);
    if (next < 0) search.focus();
    else links[Math.min(next, links.length - 1)]?.focus();
  } else if (event.key === 'Escape') {
    hideSuggestions();
    search.focus();
  }
});
document.addEventListener('click', event => {
  if (!event.target.closest('.search-wrap')) hideSuggestions();
});

const pendingVariants = new Set();
let checkoutAllowed = !document.querySelector('[data-checkout]')?.disabled;
function updateCartSummary(result) {
  if (typeof result.total !== 'number') return;
  const money = value => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0}).format(value) + ' ₽';
  document.querySelectorAll('[data-cart-total]').forEach(node => { node.textContent = money(result.total); });
  document.querySelectorAll('[data-cart-count-text]').forEach(node => { node.textContent = result.count; });
  const line = document.querySelector(`[data-cart-line="${Number(result.variant_id)}"]`);
  if (line) {
    if (result.quantity === 0) line.remove();
    else line.querySelector('[data-line-total]').textContent = money(result.line_total);
  }
  checkoutAllowed = result.can_checkout;
  document.querySelector('[data-checkout-warning]')?.toggleAttribute('hidden', checkoutAllowed);
  if (document.querySelector('.cart-list') && result.count === 0) window.location.reload();
}
function cartControls(variantId) {
  return document.querySelectorAll(`[data-cart-variant="${Number(variantId)}"]`);
}
function showCartQuantity(group, quantity, stock = Number(group.dataset.cartStock)) {
  const previousQuantity = parseInt(group.querySelector('[data-cart-quantity]').textContent, 10);
  group.dataset.cartStock = stock;
  const busy = group.classList.contains('is-updating');
  const add = group.querySelector('[data-cart-add]');
  const plus = group.querySelector('[data-cart-plus]');
  const minus = group.querySelector('[data-cart-minus]');
  add.hidden = quantity > 0;
  add.disabled = busy || stock <= 0 || group.dataset.cartAvailable === '0';
  group.querySelector('[data-cart-stepper]').hidden = quantity === 0;
  group.querySelector('[data-cart-quantity]').textContent = `${quantity} шт.`;
  if (previousQuantity !== quantity) {
    group.classList.remove('quantity-changed');
    void group.offsetWidth;
    group.classList.add('quantity-changed');
    clearTimeout(group.quantityAnimationTimer);
    group.quantityAnimationTimer = setTimeout(() => group.classList.remove('quantity-changed'), 400);
  }
  minus.value = Math.max(0, quantity - 1);
  plus.value = quantity + 1;
  minus.disabled = busy;
  plus.disabled = busy || quantity >= Math.min(99, stock) || group.dataset.cartAvailable === '0';
}

document.querySelectorAll('form[data-async]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.submitter || form.querySelector('button');
    if (button.disabled) return;
    // Передаём значение именно нажатой кнопки: добавить, минус или плюс.
    const data = new FormData(form, event.submitter);
    const variantId = form.closest('[data-cart-variant]')?.dataset.cartVariant || form.closest('[data-cart-line]')?.dataset.cartLine;
    if (variantId && pendingVariants.has(variantId)) return;
    if (variantId) {
      pendingVariants.add(variantId);
      document.querySelectorAll('[data-checkout]').forEach(button => { button.disabled = true; });
      cartControls(variantId).forEach(group => {
        group.classList.add('is-updating');
        group.querySelectorAll('button').forEach(item => { item.disabled = true; });
      });
    }
    button.disabled = true;
    button.classList.add('is-loading');
    try {
      const response = await fetch(form.action, { method: 'POST', body: data, headers: { 'X-Requested-With': 'fetch' } });
      if (response.redirected && !response.headers.get('content-type')?.includes('application/json')) {
        window.location.assign(response.url);
        return;
      }
      if (!response.ok) throw new Error('Действие не выполнено. Обновите страницу и попробуйте снова.');
      const result = await response.json();
      if (form.dataset.async !== 'quantity' || result.error) toast(result.message, Boolean(result.error));
      if (!result.error && typeof result.quantity === 'number') {
        cartControls(result.variant_id).forEach(group => showCartQuantity(group, result.quantity, result.stock));
        updateCartSummary(result);
      }
      if (form.dataset.async === 'favorite' && !result.error) {
        button.classList.toggle('is-favorite', result.active);
        button.setAttribute('aria-pressed', String(result.active));
        button.setAttribute('aria-label', result.active ? 'Удалить из избранного' : 'Добавить в избранное');
        button.classList.remove('animate-heart');
        void button.offsetWidth;
        button.classList.add('animate-heart');
        // Если одна карточка встречается ещё раз на странице, синхронизируем сердечки.
        document.querySelectorAll(`form[action="${new URL(form.action).pathname}"][data-async="favorite"] button`).forEach(other => {
          other.classList.toggle('is-favorite', result.active);
          other.setAttribute('aria-pressed', String(result.active));
        });
      }
      if (typeof result.count === 'number') {
        document.querySelectorAll('[data-cart-count]').forEach(badge => {
          badge.textContent = result.count;
          badge.hidden = result.count === 0;
          badge.classList.remove('badge-pop');
          void badge.offsetWidth;
          badge.classList.add('badge-pop');
        });
      }
    } catch (error) {
      toast(error.message || 'Не удалось связаться с сервером.', true);
    } finally {
      button.disabled = false;
      button.classList.remove('is-loading');
      if (variantId) {
        pendingVariants.delete(variantId);
        cartControls(variantId).forEach(group => {
          group.classList.remove('is-updating');
          showCartQuantity(group, parseInt(group.querySelector('[data-cart-quantity]').textContent, 10));
        });
        document.querySelectorAll('[data-checkout]').forEach(button => { button.disabled = !checkoutAllowed || pendingVariants.size > 0; });
      }
    }
  });
});

document.addEventListener('change', event => {
  if (event.target.matches('[data-role-select]')) {
    event.target.form.querySelector('[data-staff-point]').hidden = event.target.value !== 'staff';
  }
});

document.querySelectorAll('[data-pvz-issue]').forEach(form => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button');
    button.disabled = true;
    button.classList.add('is-loading');
    button.setAttribute('aria-busy', 'true');
  });
});

// Одно уведомление об отсутствии ПВЗ в выбранном городе.
if (document.body.dataset.cityHasPoints === 'false') {
  const key = 'vbsh-no-pickup:' + document.body.dataset.city;
  try {
    if (!sessionStorage.getItem(key)) {
      toast('В этом городе пока нет пунктов выдачи. Товары останутся в корзине — заказать можно позже.');
      sessionStorage.setItem(key, '1');
    }
  } catch { toast('В этом городе пока нет ПВЗ. Товары останутся в корзине.'); }
}

// Доставка работает на сервере; браузер лишь проверяет новые уведомления.
let checkingNotifications = false;
const deliveryClocks = new WeakMap();
function updateDeliveryProgress() {
  document.querySelectorAll('.delivery-animation').forEach(node => {
    if (!['created', 'shipping'].includes(node.dataset.deliveryState)) return;
    if (!deliveryClocks.has(node)) deliveryClocks.set(node, performance.now());
    const elapsed = Number(node.dataset.deliveryElapsed) + (performance.now() - deliveryClocks.get(node)) / 1000;
    node.style.setProperty('--delivery-progress', Math.min(86, 6 + elapsed / 30 * 80) + '%');
  });
}
updateDeliveryProgress();
setInterval(updateDeliveryProgress, 1000);

async function animateDeliveryArrival(states) {
  let changed = false;
  for (const state of states) {
    if (state.status !== 'ready') continue;
    const row = document.querySelector(`[data-order-id="${Number(state.id)}"]`);
    const animation = row?.querySelector('.delivery-animation');
    if (!animation || !['created', 'shipping'].includes(animation.dataset.deliveryState)) continue;
    animation.classList.add('is-arriving');
    animation.dataset.deliveryState = 'ready';
    animation.style.setProperty('--delivery-progress', '100%');
    animation.querySelector('.delivery-success').setAttribute('aria-hidden', 'false');
    const status = row.querySelector('.status');
    status.textContent = 'Готов к выдаче';
    status.className = 'status status-ready';
    changed = true;
  }
  // Даём грузовику доехать и галочке появиться, затем загружаем код получения.
  if (changed && !reducedMotion.matches) await new Promise(resolve => setTimeout(resolve, 2100));
}
async function checkNotifications() {
  if (!document.body.dataset.userId || document.hidden || checkingNotifications) return;
  checkingNotifications = true;
  try {
    const response = await fetch('/api/notifications' + (location.pathname === '/staff' ? '?scope=staff' : ''), {cache: 'no-store'});
    if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) return;
    const result = await response.json();
    if (String(result.user_id) !== document.body.dataset.userId) return;
    const key = 'vbsh-ready:' + result.user_id;
    let seen = [];
    try { seen = JSON.parse(localStorage.getItem(key) || '[]'); } catch {}
    if (!Array.isArray(seen)) seen = [];
    const fresh = result.notifications.filter(item => !seen.includes(item.id));
    for (const item of fresh) {
      toast(item.message, false, item.url);
      seen.push(item.id);
    }
    try { localStorage.setItem(key, JSON.stringify(seen.slice(-200))); } catch {}
    const ordersView = document.querySelector('[data-orders-view]');
    const deskInput = document.querySelector('[data-pvz-code]');
    if (ordersView && ordersView.dataset.liveRefresh !== 'false' && !deskInput?.value.trim()) {
      const filters = new URLSearchParams(location.search);
      const states = result.states.filter(item => location.pathname !== '/staff' ||
        ((!filters.get('status') || filters.get('status') === item.status) &&
         (!filters.get('search') || Number(filters.get('search')) === item.id)));
      const visible = Array.from(document.querySelectorAll('[data-order-id]')).map(row => ({id:Number(row.dataset.orderId),status:row.dataset.orderStatus}));
      if (JSON.stringify(states) !== JSON.stringify(visible)) {
        if (fresh.length) sessionStorage.setItem('vbsh-arrival', fresh[0].message);
        await animateDeliveryArrival(states);
        location.reload();
      }
    }
  } catch { /* Следующая проверка повторится, содержимое страницы не теряется. */ }
  finally { checkingNotifications = false; }
}
try {
  const message = sessionStorage.getItem('vbsh-arrival');
  if (message) { toast(message); sessionStorage.removeItem('vbsh-arrival'); }
} catch {}
checkNotifications();
setInterval(checkNotifications, 5000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) checkNotifications(); });

document.querySelectorAll('[data-gallery-image]').forEach(button => {
  button.addEventListener('click', () => {
    const main = document.getElementById('main-product-image');
    const zoom = document.getElementById('zoom-product-image');
    main.src = button.dataset.galleryImage;
    zoom.src = button.dataset.galleryImage;
    document.querySelectorAll('[data-gallery-image]').forEach(item => {
      item.classList.toggle('selected', item === button);
      item.setAttribute('aria-pressed', String(item === button));
    });
  });
});

document.querySelectorAll('[data-copy-code]').forEach(button => {
  button.addEventListener('click', async () => {
    const code = button.dataset.copyCode;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(code);
      } else {
        const field = document.createElement('textarea');
        field.value = code;
        field.style.cssText = 'position:fixed;left:-9999px;top:0';
        document.body.append(field);
        try {
          field.select();
          if (!document.execCommand('copy')) throw new Error('Clipboard unavailable');
        } finally {
          field.remove();
          button.focus();
        }
      }
      button.textContent = 'Скопировано ✓';
      button.classList.add('copy-success');
      button.setAttribute('aria-label', 'Код получения скопирован');
      clearTimeout(button.copyResetTimer);
      button.copyResetTimer = setTimeout(() => {
        button.textContent = 'Копировать';
        button.classList.remove('copy-success');
        button.setAttribute('aria-label', 'Скопировать код получения');
      }, 1800);
    } catch {
      toast('Не удалось скопировать код. Попробуйте ещё раз.', true);
    }
  });
});
document.querySelectorAll('[data-step]').forEach(button => {
  button.addEventListener('click', () => {
    const input = button.parentElement.querySelector('input');
    input.value = Math.min(99, Math.max(0, Number(input.value) + Number(button.dataset.step)));
    input.form.requestSubmit();
  });
});

document.addEventListener('click', event => {
  const add = event.target.closest('[data-add-variant]');
  if (!add) return;
  const row = document.createElement('div');
  row.className = 'variant-row';
  row.innerHTML = '<input type="hidden" name="variant_id" value=""><label>Размер<input name="variant_size" value="Без размера" maxlength="30" required></label><label>Цвет<input name="variant_color" maxlength="40" required></label><label>Остаток<input type="number" name="variant_stock" value="0" min="0" max="1000000" required></label>';
  add.closest('form').querySelector('[data-variant-rows]').append(row);
  row.querySelector('input[name="variant_size"]').focus();
});
let previewUrls = [];
document.addEventListener('change', event => {
  if (!event.target.matches('[data-photo-input]')) return;
  previewUrls.forEach(url => URL.revokeObjectURL(url));
  previewUrls = [];
  const previews = event.target.closest('form').querySelector('[data-photo-previews]');
  previews.replaceChildren();
  const files = Array.from(event.target.files);
  if (files.length > 8 || files.some(file => file.size > 8 * 1024 * 1024)) {
    toast('Выберите до 8 фотографий, каждая не больше 8 МБ.', true);
    event.target.value = '';
    return;
  }
  files.forEach(file => {
    const image = document.createElement('img');
    const url = URL.createObjectURL(file);
    previewUrls.push(url);
    image.src = url;
    image.alt = file.name;
    previews.append(image);
  });
});
