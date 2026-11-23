/* Небольшие интерактивные детали. Формы и данные обрабатываются сервером. */
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const toastContainer = document.querySelector('.toasts');
function toast(message, error = false) {
  const element = document.createElement('div');
  element.className = `toast${error ? ' error' : ''}`;
  element.textContent = message;
  toastContainer.append(element);
  setTimeout(() => {
    element.classList.add('leaving');
    setTimeout(() => element.remove(), 250);
  }, 3600);
}

document.addEventListener('click', event => {
  const open = event.target.closest('[data-open]');
  if (open) {
    const dialog = document.getElementById(open.dataset.open);
    if (dialog) dialog.showModal();
  }
  const close = event.target.closest('[data-close]');
  if (close) close.closest('dialog').close();
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
    if (event.target !== dialog) return;
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

// Подсказки отменяют предыдущий запрос, чтобы старый ответ не заменил новый.
const search = document.querySelector('.search input');
const suggestions = document.getElementById('search-suggestions');
let searchTimer;
let searchController;
function hideSuggestions() {
  suggestions.hidden = true;
  search.setAttribute('aria-expanded', 'false');
}
search?.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchController?.abort();
  const value = search.value.trim();
  hideSuggestions();
  if (value.length < 2) return;
  searchTimer = setTimeout(async () => {
    searchController = new AbortController();
    try {
      const response = await fetch(`/api/suggestions?q=${encodeURIComponent(value)}`, { signal: searchController.signal });
      if (!response.ok) return;
      const items = await response.json();
      suggestions.replaceChildren();
      for (const item of items) {
        const link = document.createElement('a');
        link.href = item.url;
        link.textContent = item.title;
        suggestions.append(link);
      }
      suggestions.hidden = items.length === 0;
      search.setAttribute('aria-expanded', String(items.length > 0));
    } catch (error) {
      if (error.name !== 'AbortError') hideSuggestions();
    }
  }, 220);
});
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

document.querySelectorAll('form[data-async]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    if (button.disabled) return;
    const data = new FormData(form);
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
      toast(result.message, Boolean(result.error));
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
    }
  });
});

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
document.querySelectorAll('[data-step]').forEach(button => {
  button.addEventListener('click', () => {
    const input = button.parentElement.querySelector('input');
    input.value = Math.min(99, Math.max(0, Number(input.value) + Number(button.dataset.step)));
    input.form.requestSubmit();
  });
});

document.querySelector('[data-add-variant]')?.addEventListener('click', () => {
  const row = document.createElement('div');
  row.className = 'variant-row';
  row.innerHTML = '<input type="hidden" name="variant_id" value=""><label>Размер<input name="variant_size" value="Без размера" maxlength="30" required></label><label>Цвет<input name="variant_color" maxlength="40" required></label><label>Остаток<input type="number" name="variant_stock" value="0" min="0" max="1000000" required></label>';
  document.querySelector('[data-variant-rows]').append(row);
  row.querySelector('input[name="variant_size"]').focus();
});
let previewUrls = [];
document.querySelector('[data-photo-input]')?.addEventListener('change', event => {
  previewUrls.forEach(url => URL.revokeObjectURL(url));
  previewUrls = [];
  const previews = document.querySelector('[data-photo-previews]');
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
