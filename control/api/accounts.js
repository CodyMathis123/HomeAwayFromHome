'use strict';
// Account administration is entirely server-enforced and opt-in. This module
// only displays capabilities and submits narrow operations; it cannot authorize.
const HomeAwayAccounts = (() => {
  let initialized = false;
  let mutationPending = false;
  let cachedUsers = [];
  function renderDirectory() {
    const query = ($('accounts-search')?.value || '').trim().toLocaleLowerCase();
    const role = $('accounts-filter')?.value || 'all';
    const visible = cachedUsers.filter(user =>
      (role === 'all' || (role === 'pending' ? !user.role : user.role === role)) &&
      [user.name,user.id,user.email].some(part => String(part || '').toLocaleLowerCase().includes(query)));
    $('accounts-users').replaceChildren(...visible.map(renderUser));
    if (!visible.length) $('accounts-users').textContent = cachedUsers.length ? 'No users match these filters.' : 'No accounts found.';
    $('accounts-count').textContent = visible.length + ' of ' + cachedUsers.length + ' users';
  }
  const $ = id => document.getElementById(id);
  const make = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const message = (text, state = 'info') => {
    const element = $('accounts-message');
    element.textContent = text;
    element.dataset.state = state;
  };
  async function api(path, data) {
    const request = data === undefined ?
      {credentials:'same-origin',cache:'no-store'} :
      {method:'POST',credentials:'same-origin',
       headers:{'Content-Type':'application/json','X-HomeAway-Request':'1'},
       body:JSON.stringify(data)};
    const response = await fetch('/control/api/admin/users' + path,request);
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'Account request failed');
    return result;
  }
  async function mutate(path, data, successMessage) {
    if (mutationPending) {
      message('Another account change is already in progress. Review its result first.');
      return false;
    }
    mutationPending = true;
    const createButton = $('accounts-add').querySelector('button[type="submit"]');
    createButton.disabled = true;
    try {
      await api(path,data);
      const refreshed = await refresh();
      message(refreshed ?
        (successMessage || 'Account directory updated. Permissions are checked again on the next request.') :
        'Change succeeded, but the user list could not refresh. Use Refresh users to verify.',
        refreshed ? 'success' : 'warning');
      return true;
    } catch (err) {
      message('Change failed: ' + err.message + '. No success was confirmed; check the list before retrying.', 'error');
      return false;
    } finally {
      mutationPending = false;
      createButton.disabled = false;
    }
  }
  function renderUser(user) {
    const card = make('article','account-user');
    const title = make('div','top');
    title.append(make('strong','',user.name),make('code','account-id',user.id));
    card.append(title,make('p','account-detail',user.email));
    card.append(make('p','account-detail',user.role ?
      'HomeAway role: ' + user.role :
      'No HomeAway access · pending or revoked (password status unknown)'));
    if (user.id === 'admin') {
      card.append(make('p','account-detail','Directory bootstrap administrator · protected'));
      return card;
    }
    const control = make('div','account-buttons');
    const select = make('select','');
    select.setAttribute('aria-label', 'HomeAway role for ' + user.id);
    // No default grant: pending or revoked users must get an explicit selection.
    const roleValues = ['viewers', 'operators', 'admins'];
    const placeholder = make('option', '', 'Choose a HomeAway role');
    placeholder.value = '';
    placeholder.disabled = true;
    placeholder.selected = !roleValues.includes(user.role);
    select.append(placeholder);
    for(const [value,label] of [
      ['viewers','Viewer · status only'],['operators','Operator · wake/sleep'],
      ['admins','Administrator · manage and power']]) {
      const option = make('option','',label);
      option.value = value;
      if (user.role === value) option.selected = true;
      select.append(option);
    }
    const save = make('button','','Set role');
    save.type='button';
    save.disabled = true;
    select.addEventListener('change', () => {
      save.disabled = !select.value || user.role === select.value;
    });
    save.addEventListener('click', async () => {
      if (!select.value || user.role === select.value) return;
      if (!await HomeAwayAdminDialog.confirm('Change HomeAway permissions for ' + user.id + '?')) return;
      await mutate('/set-role',{id:user.id,revision:user.revision,role:select.value});
    });
    const revoke = make('button','danger','Revoke HomeAway access');
    revoke.type='button';
    revoke.disabled = !user.homeawayAccess;
    revoke.addEventListener('click', async () => {
      if (!await HomeAwayAdminDialog.confirm('Revoke all HomeAway access for ' + user.id +
                   '? This does not disable their LDAP password or other applications.')) return;
      await mutate('/revoke',{id:user.id,revision:user.revision});
    });
    if (!user.role) {
      const invite = make('button','','Send setup invitation');
      invite.type='button';
      invite.addEventListener('click', async () => {
        if (!await HomeAwayAdminDialog.confirm('Email a password-setup link to ' + user.email +
                     '? Confirm the address belongs to the intended person.')) return;
        const ok=await mutate('/invite',{id:user.id,revision:user.revision},
          'Password-setup email requested for ' + user.email +
          '. Delivery is not independently confirmed; ask the recipient to check their inbox.');
        if (!ok) message('Invitation not confirmed. Check the user list before retrying.','warning');
      });
      control.append(invite);
    }
    const remove=make('button','danger','Delete user');
    remove.type='button';
    remove.disabled=!!user.role;
    remove.title=user.role ? 'Revoke HomeAway access before deleting this account' :
                           'Permanently remove this directory account';
    remove.addEventListener('click', async () => {
      if (user.role) return;
      const answer=await HomeAwayAdminDialog.input('Permanently delete ' + user.id +
        ' and their directory credentials? Type the exact username to confirm.');
      if (answer!==user.id) {
        if (answer!==null) message('Deletion cancelled: username did not match.','warning');
        return;
      }
      await mutate('/delete',{id:user.id,revision:user.revision,confirm:answer},
                   'Directory account ' + user.id + ' deleted. Existing sessions may require revocation.');
    });
    control.append(select,save,revoke,remove);
    card.append(control);
    return card;
  }
  async function refresh() {
    const btn=$('accounts-refresh');
    btn.disabled=true;
    try {
      const result=await api('');
      cachedUsers = result.users;
      renderDirectory();
      return true;
    } catch (err) {
      message('Account list unavailable: ' + err.message, 'error');
      return false;
    } finally { btn.disabled=false; }
  }
  function start(identity) {
    const section=$('accounts');
    if (!section || !identity?.capabilities?.manageUsers) {
      if (section) section.hidden=true;
      return;
    }
    section.hidden=false;
    if (initialized) return;
    initialized=true;
    $('accounts-refresh').addEventListener('click',refresh);
    $('accounts-search').addEventListener('input',renderDirectory);
    $('accounts-filter').addEventListener('change',renderDirectory);
    // Canonical directory usernames are lowercase. Normalize as the user types
    // so native pattern validation doesn't block mixed-case names silently.
    const usernameInput=$('accounts-add').elements.namedItem('username');
    usernameInput.addEventListener('input', () => {
      const normalized=usernameInput.value.toLowerCase();
      if (usernameInput.value !== normalized) usernameInput.value=normalized;
    });
    $('accounts-add').addEventListener('submit',async event => {
      event.preventDefault();
      const form=event.currentTarget;
      const user=form.elements.namedItem('username').value.trim().toLowerCase();
      const name=form.elements.namedItem('displayname').value.trim();
      const email=form.elements.namedItem('email').value.trim();
      const invite=form.elements.namedItem('sendinvite').checked;
      if (!await HomeAwayAdminDialog.confirm('Create pending account ' + user + (invite ?
                   ' and request a setup email for ' + email :
                   ' without an invitation') +
                   '? No HomeAway role will be granted.')) return;
      if (mutationPending) return;
      mutationPending = true;
      const createButton=form.querySelector('button[type="submit"]');
      createButton.disabled=true;
      try {
        const result=await api(invite ? '/create-invite' : '/create',
                               {id:user,name,email});
        const refreshed=await refresh();
        if (result.invitationError) {
          message(result.invitationError + ' Account ' + user +
                  ' exists without a HomeAway role. Retry via Send setup invitation.','warning');
        } else if (result.invitationRequested) {
          message('Created pending account ' + user +
                  ' and requested its password-setup email. No HomeAway role was granted. ' +
                  'Ask the recipient to check their inbox.','success');
        } else {
          message('Created pending account ' + user +
                  '. Use Send setup invitation to email the password link.','success');
        }
        if (!refreshed) message('Account created but list refresh failed. Refresh before retrying.', 'warning');
        form.reset();
      } catch(err) {
        message('Create failed: ' + err.message +
                '. Check the user list before retrying.','error');
      } finally {
        mutationPending=false;
        createButton.disabled=false;
      }
    });
    void refresh();
  }
  return {start,refresh};
})();
