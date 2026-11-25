/* Окна используют те же формы и проверки Flask, что и обычные страницы. */
const managementDialog = document.getElementById('management-dialog');
let managementBusy = false;
let editorRequest = null;

function managementError(message) {
  const error = managementDialog.querySelector('.dialog-error');
  error.textContent = message;
  error.hidden = false;
  error.scrollIntoView({block: 'nearest', behavior: 'smooth'});
}

function showManagementDialog(title, content, wide = false) {
  managementDialog.classList.toggle('product-modal', wide);
  managementDialog.querySelector('h2').textContent = title;
  managementDialog.querySelector('.dialog-error').hidden = true;
  managementDialog.querySelector('[data-dialog-content]').replaceChildren(content);
  if (!managementDialog.open) managementDialog.showModal();
  managementDialog.scrollTop = 0;
}

function parseManagementPage(html) {
  return new DOMParser().parseFromString(html, 'text/html');
}

function applyManagementPage(page) {
  const replacement = page.querySelector('[data-management]');
  const current = document.querySelector('[data-management]');
  if (!replacement || !current) throw new Error('Не удалось обновить список. Обнови страницу, чтобы проверить результат.');
  current.replaceChildren(...replacement.childNodes);
  // Категории для нового товара обновляются вместе с каталогом.
  const fields = page.getElementById('new-product-fields');
  if (fields) document.getElementById('new-product-fields').replaceWith(fields);
}

document.addEventListener('click', async event => {
  const productLink = event.target.closest('[data-product-dialog]');
  const formButton = event.target.closest('[data-form-dialog]');
  const sectionLink = event.target.closest('.admin-tabs a');
  if (!productLink && !formButton && !sectionLink) return;
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
  if (sectionLink && new URL(sectionLink.href).pathname !== '/admin') return;
  event.preventDefault();
  if (managementBusy) return;
  if (formButton) {
    showManagementDialog(formButton.dataset.dialogTitle,
      document.getElementById(formButton.dataset.formDialog).content.cloneNode(true));
    managementDialog.querySelector('input:not([type="hidden"])')?.focus();
    return;
  }
  if (productLink) {
    editorRequest?.abort();
    if (new URL(productLink.href).pathname.endsWith('/new')) {
      showManagementDialog('Новый товар', document.getElementById('new-product-fields').content.cloneNode(true), true);
      managementDialog.querySelector('[name="title"]').focus();
      return;
    }
    const controller = new AbortController();
    editorRequest = controller;
    const loading = document.createElement('div');
    loading.className = 'dialog-loading';
    loading.setAttribute('role', 'status');
    loading.textContent = 'Загружаем товар…';
    showManagementDialog(productLink.dataset.productDialog, loading, true);
    try {
      const response = await fetch(productLink.href, {signal: controller.signal, cache: 'no-store'});
      const page = parseManagementPage(await response.text());
      const form = page.querySelector('.product-editor');
      if (!response.ok || !form) throw new Error('Не удалось загрузить товар. Закрой окно и попробуй ещё раз.');
      if (!managementDialog.open || controller !== editorRequest) return;
      showManagementDialog(productLink.dataset.productDialog, form, true);
      form.querySelector('[name="title"]').focus();
    } catch (error) {
      if (error.name !== 'AbortError') managementError(error.message);
    }
    return;
  }
  managementBusy = true;
  const root = document.querySelector('[data-management]');
  root.classList.add('section-loading');
  root.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch(sectionLink.href, {cache: 'no-store'});
    const page = parseManagementPage(await response.text());
    if (!response.ok) throw new Error('Раздел не загрузился. Попробуй ещё раз.');
    applyManagementPage(page);
    history.pushState(null, '', sectionLink.href);
  } catch (error) { toast(error.message, true); }
  finally {
    root.classList.remove('section-loading');
    root.removeAttribute('aria-busy');
    managementBusy = false;
  }
});

document.addEventListener('submit', async event => {
  const form = event.target;
  if (!form.closest('[data-management], #management-dialog')) return;
  event.preventDefault();
  if (managementBusy) return;
  managementBusy = true;
  const inDialog = form.closest('dialog');
  const button = event.submitter || form.querySelector('[type="submit"], button:not([type="button"])');
  const original = button?.textContent;
  const body = new FormData(form);
  if (event.submitter?.name) body.append(event.submitter.name, event.submitter.value);
  if (inDialog) {
    inDialog.setAttribute('aria-busy', 'true');
    inDialog.querySelector('.dialog-error').hidden = true;
  }
  if (button) {
    button.disabled = true;
    button.classList.add('is-loading');
    button.textContent = 'Сохраняем…';
  }
  try {
    const response = await fetch(form.action || location.href, {method: 'POST', body, headers: {'X-Requested-With': 'management'}});
    const page = parseManagementPage(await response.text());
    const message = page.querySelector('.messages')?.textContent.trim();
    if (!response.ok) throw new Error(message || 'Не удалось сохранить. Проверь поля и попробуй ещё раз.');
    applyManagementPage(page);
    if (inDialog) inDialog.close();
    toast(message || 'Сохранено');
  } catch (error) {
    const message = error instanceof TypeError ? 'Нет связи с сервером. Данные остались в форме. Перед повторным сохранением проверь список.' : error.message;
    if (inDialog) managementError(message);
    else toast(message, true);
  } finally {
    managementBusy = false;
    inDialog?.removeAttribute('aria-busy');
    if (button) {
      button.disabled = false;
      button.classList.remove('is-loading');
      button.textContent = original;
    }
  }
});

managementDialog?.addEventListener('cancel', event => {
  if (managementBusy) event.preventDefault();
});
managementDialog?.addEventListener('close', () => {
  editorRequest?.abort();
  previewUrls.forEach(url => URL.revokeObjectURL(url));
  previewUrls = [];
});
document.addEventListener('click', event => {
  if (event.target.closest('#management-dialog .editor-save > a')) {
    event.preventDefault();
    if (!managementBusy) managementDialog.close();
  }
});
window.addEventListener('popstate', () => {
  if (document.querySelector('[data-management]')) location.reload();
});
