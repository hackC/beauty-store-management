/* Two-level selection on the existing service catalogue. */
(() => {
  'use strict';
  const uncategorized = '';
  const typeOf = service => service?.categoryId || uncategorized;

  function available(services, preservedId) {
    return services.filter(service => service.enabled !== false || service.id === preservedId);
  }

  function types(data, preservedId) {
    const used = new Set(available(data.services, preservedId).map(typeOf));
    const result = (data.serviceCategories || []).filter(type => used.has(type.id));
    if (used.has(uncategorized)) result.push({id: uncategorized, name: '未分类'});
    return result;
  }

  function forType(data, typeId, preservedId) {
    return available(data.services, preservedId).filter(service => typeOf(service) === typeId);
  }

  function remark(data, appointment) {
    return appointment.projectName || data.services.find(service => service.id === appointment.serviceId)?.name || '';
  }

  function projectIds(appointment) {
    return appointment.projectIds?.length ? appointment.projectIds : appointment.serviceId ? [appointment.serviceId] : [];
  }

  function fullNames(data, appointment, catalogue = new Map(data.services.map(service => [service.id, service.name]))) {
    const names = projectIds(appointment).map(id => catalogue.get(id)?.trim());
    if (!names.some(Boolean)) return [appointment.projectName?.trim() || '已删除项目'];
    return names.map(name => name || '已移除项目');
  }

  const joinNames = names => names.join('＋');
  const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter('zh', {granularity:'grapheme'}) : null;

  function fitNames(names, fits, similarNames = []) {
    const full = joinNames(names);
    if (fits(full)) return full;
    for (let count = names.length - 1; count > 0; count--) {
      const candidate = joinNames(names.slice(0,count)) + ' +' + (names.length-count);
      if (fits(candidate)) return candidate;
    }
    const more = names.length > 1 ? ' +' + (names.length-1) : '';
    const split = text => segmenter ? Array.from(segmenter.segment(text), item => item.segment) : Array.from(text);
    const chars = split(names[0] || '');
    const others = similarNames.filter(name => name.startsWith(chars.slice(0,2).join('')) && name !== names[0]).map(split);
    const trim = (letters, head, tail) => letters.length <= head+tail ? letters.join('') :
      letters.slice(0,head).join('') + '…' + (tail ? letters.slice(-tail).join('') : '');
    let fallback;
    for (let retained = chars.length-1; retained>0; retained--) {
      const preferredHead = Math.max(1,Math.floor(retained/3));
      const choices = Array.from({length:retained}, (_,i)=>i+1)
        .sort((a,b)=>Math.abs(a-preferredHead)-Math.abs(b-preferredHead));
      for (const head of choices) {
        const tail = retained-head;
        const clipped = trim(chars,head,tail);
        if (!fits(clipped+more)) continue;
        fallback ??= clipped+more;
        if (!others.some(other => trim(other,head,tail) === clipped)) return clipped+more;
      }
    }
    return fallback || '…'+more;
  }

  function shortName(data, service) {
    const name = service.name || '';
    const han = name.match(/\p{Script=Han}/gu);
    const letters = han || [...name];
    const category = (data.serviceCategories || []).find(item => item.id === service.categoryId);
    if (/套餐|[+＋]/.test(name) || /套餐/.test(category?.name || '')) {
      const prefix = letters.slice(0, 3).join('');
      const suffix = letters.slice(3, 6).join('');
      return suffix ? prefix + '+' + suffix : prefix;
    }
    return letters.slice(0, 3).join('');
  }

  function totals(data, ids, together) {
    const services = ids.map(id => data.services.find(service => service.id === id)).filter(Boolean);
    const durations = services.map(service => Number(service.duration) || 0);
    return {
      duration: together ? Math.max(0, ...durations) : durations.reduce((sum, value) => sum + value, 0),
      buffer: Math.max(0, ...services.map(service => Number(service.buffer) || 0)),
      projectName: services.map(service => shortName(data, service)).join('+'),
    };
  }

  function mount(root, data, selection, onChange) {
    const ids = [...(selection.ids.length ? selection.ids : [''])];
    let together = Boolean(selection.together);
    let disabled = false;
    let openIndex = -1;
    let activeType = null;

    const node = (tag, label) => {
      const element = document.createElement(tag);
      if (label) element.textContent = label;
      return element;
    };
    function button(label, handler) {
      const control = node('button', label);
      control.type = 'button';
      control.className = 'btn';
      control.disabled = disabled;
      control.onclick = handler;
      return control;
    }
    function state() {
      return {ids: ids.filter(Boolean), together: ids.filter(Boolean).length > 1 && together,
              complete: ids.every(Boolean)};
    }
    function changed() {
      const selected = state();
      onChange(selected, totals(data, selected.ids, selected.together));
      render();
    }

    function render() {
      root.replaceChildren();
      ids.forEach((id, index) => {
        const item = node('div');
        item.className = 'project-pick-row';
        const service = data.services.find(project => project.id === id);
        const toggle = button(service?.name || (id ? '历史项目（已移除）' : '选择项目'), () => {
          openIndex = openIndex === index ? -1 : index;
          activeType = null;
          render();
          root.querySelectorAll('.project-pick-toggle')[index]?.focus();
        });
        toggle.classList.add('project-pick-toggle');
        toggle.setAttribute('aria-expanded', String(openIndex === index));
        item.append(toggle);
        if (ids.length > 1 && !disabled) {
          const remove = button('移除', () => {ids.splice(index, 1);openIndex=-1;changed();});
          remove.setAttribute('aria-label', '移除' + (service?.name || '项目'));
          item.append(remove);
        }
        root.append(item);
        if (openIndex !== index || disabled) return;
        const menu = node('div');
        menu.className = 'project-pick-menu';
        if (activeType === null) {
          menu.append(node('strong', '选择项目类型'));
          for (const type of types(data, id)) {
            menu.append(button(type.name + ' ›', () => {activeType=type.id;render();root.querySelector('.project-pick-menu button')?.focus();}));
          }
        } else {
          menu.append(button('‹ 返回项目类型', () => {activeType=null;render();root.querySelector('.project-pick-menu button')?.focus();}));
          for (const project of forType(data, activeType, id)) {
            if (ids.some((existing, position) => position !== index && existing === project.id)) continue;
            menu.append(button(project.name, () => {ids[index]=project.id;openIndex=-1;changed();root.querySelectorAll('.project-pick-toggle')[index]?.focus();}));
          }
        }
        root.append(menu);
      });
      if (!disabled) {
        root.append(button('＋ 增加项目', () => {ids.push('');openIndex=ids.length-1;activeType=null;changed();root.querySelector('.project-pick-menu button')?.focus();}));
      }
      if (ids.filter(Boolean).length > 1) {
        const label = node('label');
        label.className = 'project-pick-together';
        const checkbox = node('input');
        checkbox.type = 'checkbox';
        checkbox.checked = together;
        checkbox.disabled = disabled;
        checkbox.onchange = () => {together=checkbox.checked;changed();root.querySelector('input[type=checkbox]')?.focus();};
        label.append(checkbox, document.createTextNode('项目同时做（按最长项目计时）'));
        root.append(label);
      }
      const output = node('output', totals(data, state().ids, together).projectName || selection.remark || '—');
      output.setAttribute('aria-live','polite');
      const note = node('div');
      note.className = 'project-pick-remark';
      note.append(node('strong', '项目备注'), output);
      root.append(note);
    }
    render();
    return {
      state,
      setDisabled(value) {disabled=Boolean(value);if(disabled)openIndex=-1;render();},
    };
  }

  window.ScheduleProjects = Object.freeze({typeOf, types, forType, remark, projectIds, fullNames, fitNames, totals, mount});
})();
