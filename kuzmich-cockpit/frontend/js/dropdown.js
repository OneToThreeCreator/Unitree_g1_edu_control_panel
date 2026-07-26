'use strict';

class CustomDropdown {
  constructor({ trigger, items, onSelect, render, searchPlaceholder }) {
    this.trigger = trigger;
    this.items = items || [];
    this.filteredItems = [...this.items];
    this.onSelect = onSelect;
    this.renderItem = render || (item => item);
    this.searchPlaceholder = searchPlaceholder || 'Поиск…';
    this.selected = null;
    this.isOpen = false;

    this._build();
    this._bindEvents();
  }

  _build() {
    this.trigger.classList.add('dd-trigger');

    this.panel = document.createElement('div');
    this.panel.className = 'dd-panel';
    this.panel.style.display = 'none';

    if (this.items.length > 5) {
      this.search = document.createElement('input');
      this.search.type = 'text';
      this.search.className = 'dd-search';
      this.search.placeholder = this.searchPlaceholder;
      this.panel.appendChild(this.search);
    } else {
      this.search = null;
    }

    this.list = document.createElement('div');
    this.list.className = 'dd-list';
    this.panel.appendChild(this.list);

    this.trigger.parentNode.insertBefore(this.panel, this.trigger.nextSibling);
    this._renderList();
  }

  _bindEvents() {
    this.trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggle();
    });

    if (this.search) {
      this.search.addEventListener('input', () => {
        const q = this.search.value.toLowerCase();
        this.filteredItems = this.items.filter(item => {
          const name = typeof item === 'string' ? item : (item.name || item);
          return name.toLowerCase().includes(q);
        });
        this._renderList();
      });
    }

    document.addEventListener('click', (e) => {
      if (!this.panel.contains(e.target) && !this.trigger.contains(e.target)) {
        this.close();
      }
    });
  }

  _renderList() {
    this.list.innerHTML = '';
    const items = this.search ? this.filteredItems : this.items;

    if (items.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'dd-empty';
      empty.textContent = 'Пусто';
      this.list.appendChild(empty);
      return;
    }

    items.forEach(item => {
      const name = typeof item === 'string' ? item : (item.name || item);
      const div = document.createElement('div');
      div.className = 'dd-item' + (name === this.selected ? ' active' : '');
      div.innerHTML = this.renderItem(item, name);
      div.addEventListener('click', () => {
        this.setSelected(name);
        this.close();
        if (this.onSelect) this.onSelect(item);
      });
      this.list.appendChild(div);
    });
  }

  setItems(items) {
    this.items = items || [];
    this.filteredItems = [...this.items];
    if (this.search) this.search.value = '';
    this._renderList();
  }

  setSelected(name) {
    this.selected = name;
    this._renderList();
    this.trigger.textContent = name || '—';
  }

  open() {
    this.panel.style.display = 'block';
    this.trigger.classList.add('open');
    this.isOpen = true;
    if (this.search) {
      this.search.value = '';
      this.filteredItems = [...this.items];
      this._renderList();
      this.search.focus();
    }
  }

  close() {
    this.panel.style.display = 'none';
    this.trigger.classList.remove('open');
    this.isOpen = false;
  }

  toggle() {
    if (this.isOpen) this.close();
    else this.open();
  }
}
