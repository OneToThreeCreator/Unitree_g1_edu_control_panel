'use strict';

class SidebarEditor {
  constructor(opts) {
    this.nameInput = opts.nameInput;
    this.contentArea = opts.contentArea;
    this.dirtyIndicator = opts.dirtyIndicator;
    this.statusDiv = opts.statusDiv;
    this.saveBtn = opts.saveBtn;
    this.deleteBtn = opts.deleteBtn;
    this.editorEl = opts.editorEl;
    this.emptyEl = opts.emptyEl;
    this.actionsEl = opts.actionsEl;

    this.current = null;
    this.originalName = '';
    this.originalContent = '';
    this.isNew = false;
  }

  isDirty() {
    const name = this.nameInput.value.trim();
    const content = this.getContent();
    if (this.isNew) return name !== '' || content !== '';
    return name !== this.originalName || content !== this.originalContent;
  }

  getContent() {
    return this.contentArea.value;
  }

  setContent(val) {
    this.contentArea.value = val;
  }

  updateDirty() {
    this.dirtyIndicator.textContent = this.isDirty() ? '* не сохранён' : '';
  }

  showMsg(msg, type) {
    showStatus(this.statusDiv, msg, type);
  }

  hideEditor() {
    this.editorEl.style.display = 'none';
    if (this.emptyEl) this.emptyEl.style.display = 'flex';
    this.actionsEl.style.display = 'none';
    this.current = null;
    this.originalName = '';
    this.originalContent = '';
    this.nameInput.value = '';
    this.setContent('');
    this.dirtyIndicator.textContent = '';
    this.isNew = false;
  }

  showEditor() {
    if (this.emptyEl) this.emptyEl.style.display = 'none';
    this.editorEl.style.display = 'flex';
    this.actionsEl.style.display = 'flex';
  }

  load(name, content) {
    this.current = name;
    this.originalName = name;
    this.originalContent = content;
    this.isNew = false;
    this.nameInput.value = name;
    this.setContent(content);
    this.updateDirty();
  }

  startNew() {
    this.current = null;
    this.originalName = '';
    this.originalContent = '';
    this.isNew = true;
    this.nameInput.value = '';
    this.setContent('');
    this.dirtyIndicator.textContent = '';
    this.showEditor();
    this.nameInput.focus();
  }

  async save({ saveFn, renameFn, afterSave }) {
    const newName = this.nameInput.value.trim();
    if (!newName) {
      this.showMsg('Введите имя файла', 'err');
      return;
    }
    const content = this.getContent();

    if (this.isNew) {
      try {
        await saveFn(newName, content, true);
        this.isNew = false;
        this.originalName = newName;
        this.originalContent = content;
        this.dirtyIndicator.textContent = '';
        this.showMsg('"' + newName + '" создан', 'ok');
        if (afterSave) await afterSave();
      } catch (e) {
        this.showMsg('Ошибка: ' + e.message, 'err');
      }
      return;
    }

    const nameChanged = newName !== this.originalName;
    const contentChanged = content !== this.originalContent;

    if (!nameChanged && !contentChanged) {
      this.showMsg('Нет изменений', 'ok');
      return;
    }

    if (nameChanged && renameFn) {
      try {
        await renameFn(this.originalName, newName);
      } catch (e) {
        this.showMsg('Ошибка переименования: ' + e.message, 'err');
        return;
      }
    }

    if (contentChanged) {
      try {
        await saveFn(newName, content, false);
      } catch (e) {
        this.showMsg('Ошибка сохранения: ' + e.message, 'err');
        return;
      }
    }

    this.originalName = newName;
    this.originalContent = content;
    this.dirtyIndicator.textContent = '';
    this.showMsg('"' + newName + '" сохранён', 'ok');
    if (afterSave) await afterSave();
  }

  async deleteItem({ deleteFn, afterDelete }) {
    if (!this.current && !this.isNew) {
      this.showMsg('Нет выбранного элемента', 'err');
      return;
    }
    const label = this.nameInput.value.trim() || this.current || 'элемент';
    if (!confirm('Удалить "' + label + '"?')) return;
    try {
      if (this.isNew) {
        this.hideEditor();
        this.showMsg('Отменено', 'ok');
        return;
      }
      await deleteFn(this.current);
      this.showMsg('"' + label + '" удалён', 'ok');
      this.hideEditor();
      if (afterDelete) await afterDelete();
    } catch (e) {
      this.showMsg('Ошибка: ' + e.message, 'err');
    }
  }

  bindAll({ saveFn, renameFn, afterSave, deleteFn, afterDelete }) {
    bindCtrlSSave(this.saveBtn);

    this.nameInput.addEventListener('input', () => this.updateDirty());
    this.contentArea.addEventListener('input', () => this.updateDirty());

    this.saveBtn.addEventListener('click', () => {
      this.save({ saveFn, renameFn, afterSave });
    });

    this.deleteBtn.addEventListener('click', () => {
      this.deleteItem({ deleteFn, afterDelete });
    });
  }
}
