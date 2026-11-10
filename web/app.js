const CATEGORIES = ["Electronics", "Keys", "Clothing", "Bags", "Books", "Accessories", "Other"];
const CATEGORY_ICONS = {
  Electronics: "🎧", Keys: "🔑", Clothing: "🧥", Bags: "🎒",
  Books: "📚", Accessories: "🕶️", Other: "✳",
};
const state = {
  token: sessionStorage.getItem("campus-find-token"),
  user: null,
  view: "discover",
  filter: "all",
  category: "All",
  search: "",
  items: [],
  myItems: [],
  matches: [],
  claims: [],
  handovers: [],
  authMode: "login",
};

const $ = (selector, root = document) => root.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[character]));

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch {
    throw new Error("Cannot reach CUT Lost & Found. Check your connection and try again.");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && state.token) {
      state.token = null;
      sessionStorage.removeItem("campus-find-token");
      showAuth();
    }
    throw new Error(payload.error || "Something went wrong. Please try again.");
  }
  return payload;
}

function showAuth() {
  $("#auth-screen").classList.remove("hidden");
  $("#app-shell").classList.add("hidden");
}

function showApp() {
  if (!state.user.is_manager && state.view === "management") {
    state.view = "discover";
  }
  $("#auth-screen").classList.add("hidden");
  $("#app-shell").classList.remove("hidden");
  $("#sidebar-name").textContent = state.user.name;
  $("#sidebar-avatar").textContent = state.user.name.trim().charAt(0).toUpperCase();
  $("#campus-name").textContent = state.user.university;
  document.querySelectorAll('[data-view="management"]').forEach((link) => {
    link.classList.toggle("hidden", !state.user.is_manager);
  });
  renderView();
}

function setAuthMode(mode) {
  state.authMode = mode;
  const register = mode === "register";
  $("#auth-title").textContent = register ? "Join CUT Lost & Found." : "Lost something? Let's find it.";
  $("#auth-submit").innerHTML = register ? 'Create account <span aria-hidden="true">→</span>' : 'Sign in <span aria-hidden="true">→</span>';
  $("#auth-switch-copy").textContent = register ? "Already registered?" : "New to CUT Lost & Found?";
  $("#auth-toggle").textContent = register ? "Sign in" : "Create an account";
  $("#name-wrap").classList.toggle("hidden", !register);
  $("#auth-name").required = register;
  $("#auth-password").autocomplete = register ? "new-password" : "current-password";
  $("#auth-error").textContent = "";
}

async function handleAuth(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = $("#auth-submit");
  const payload = Object.fromEntries(new FormData(form));
  $("#auth-error").textContent = "";
  submit.disabled = true;
  submit.textContent = state.authMode === "register" ? "Creating account…" : "Signing in…";
  try {
    const result = await api(`/api/auth/${state.authMode === "register" ? "register" : "login"}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.token = result.token;
    state.user = result.user;
    sessionStorage.setItem("campus-find-token", state.token);
    await loadData();
    showApp();
  } catch (error) {
    $("#auth-error").textContent = error.message;
    submit.innerHTML = state.authMode === "register" ? 'Create account <span aria-hidden="true">→</span>' : 'Sign in <span aria-hidden="true">→</span>';
  } finally {
    submit.disabled = false;
  }
}

async function loadData() {
  const [me, items, myItems, matches, claims] = await Promise.all([
    api("/api/me"), api("/api/items"), api("/api/items?mine=1"), api("/api/matches"), api("/api/claims"),
  ]);
  state.user = me.user;
  state.items = items.items;
  state.myItems = myItems.items;
  state.matches = matches.matches;
  state.claims = claims.claims;
  state.handovers = me.user.is_manager
    ? (await api("/api/management/handovers")).handovers
    : [];
  $("#match-count").textContent = state.matches.length ? state.matches.length : "";
  const pendingIncoming = state.claims.filter((claim) => claim.direction === "incoming" && claim.status === "pending").length;
  $("#request-count").textContent = pendingIncoming ? pendingIncoming : "";
}

function dateLabel(dateString) {
  const date = new Date(`${dateString}T12:00:00`);
  if (Number.isNaN(date.getTime())) return dateString;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}

function itemCard(item, options = {}) {
  const own = Number(item.user_id) === Number(state.user.id);
  return `
    <article class="listing-card">
      <div class="item-illustration" data-category="${escapeHtml(item.category)}">${CATEGORY_ICONS[item.category] || "✳"}</div>
      <div class="listing-main">
        <div class="listing-topline">
          <span class="type-badge ${item.kind === "found" ? "found" : ""}">${item.kind === "found" ? "Found" : "Lost"}</span>
          ${options.score !== undefined ? `<span class="match-score">✳ ${options.score}% match</span>` : `<span class="listing-date">${dateLabel(item.happened_on)}</span>`}
        </div>
        <h3 class="listing-title">${escapeHtml(item.title)}</h3>
        <p class="listing-desc">${escapeHtml(item.description)}</p>
        <div class="listing-meta"><span>⌖ ${escapeHtml(item.location)}</span><span class="meta-dot">·</span><span>${escapeHtml(item.category)}</span>${!own ? `<span class="listing-owner">${escapeHtml(item.owner_name)}</span>` : ""}</div>
      </div>
    </article>`;
}

function emptyState(icon, title, copy, button = "") {
  return `<div class="empty-state"><div class="empty-icon">${icon}</div><h3>${title}</h3><p>${copy}</p>${button}</div>`;
}

function renderDashboard() {
  const recent = state.items.slice(0, 4);
  const openOwn = state.myItems.filter((item) => item.status === "open");
  const activeMatches = state.matches.length;
  const today = new Intl.DateTimeFormat(undefined, { weekday: "long" }).format(new Date()).toUpperCase();
  return `
    <div class="page-heading"><div><div class="greeting-label">${today}, CAMPUS COMMUNITY</div><h1>Hi, ${escapeHtml(state.user.name.split(/\s+/)[0])} <span aria-hidden="true">✳</span></h1><p>Here's what's happening around ${escapeHtml(state.user.university)}.</p></div></div>
    <section class="hero">
      <div class="hero-copy"><div class="hero-kicker">CHINHOYI UNIVERSITY OF TECHNOLOGY</div><h2>Let's get it back where it belongs.</h2><p>Found items are handed over through CUT management and recorded for review.</p><div class="hero-actions"><button class="button button-lime" data-action="new-post">＋ Report an item</button><button class="button button-ghost" data-action="browse">Browse reports <span aria-hidden="true">→</span></button></div></div>
      <div class="hero-art" aria-hidden="true"><span></span></div>
    </section>
    <section class="stat-grid" aria-label="Community activity">
      <div class="stat-card"><div class="stat-icon">▤</div><div class="stat-copy"><span class="stat-value">${state.items.length}</span><span class="stat-label">Active campus posts</span></div></div>
      <div class="stat-card"><div class="stat-icon">✳</div><div class="stat-copy"><span class="stat-value">${activeMatches}</span><span class="stat-label">Possible matches for you</span></div></div>
      <div class="stat-card"><div class="stat-icon">⌂</div><div class="stat-copy"><span class="stat-value">${openOwn.length}</span><span class="stat-label">Your active posts</span></div></div>
    </section>
    <section><div class="section-heading"><h2>Recent CUT reports</h2><button data-action="browse">See all reports →</button></div>
      <div class="listing-list">${recent.length ? recent.map((item) => itemCard(item)).join("") : emptyState("⌕", "A fresh start", "Be the first to post a lost or found item.", '<button class="button button-primary" data-action="new-post">Post an item</button>')}</div>
    </section>`;
}

function renderDiscover() {
  const filtered = state.items.filter((item) => {
    const typeMatches = state.filter === "all" || item.kind === state.filter;
    const categoryMatches = state.category === "All" || item.category === state.category;
    const searchText = `${item.title} ${item.description} ${item.location}`.toLowerCase();
    return typeMatches && categoryMatches && searchText.includes(state.search.toLowerCase());
  });
  return `
    <div class="page-heading"><div><div class="greeting-label">CHINHOYI UNIVERSITY OF TECHNOLOGY</div><h1>Discover</h1><p>A good place to start looking — or lending a hand.</p></div><button class="button button-primary" data-action="new-post">＋ New report</button></div>
    <div class="filter-bar"><label class="search-box"><span>⌕</span><input id="item-search" type="search" placeholder="Search an item, place, or detail…" value="${escapeHtml(state.search)}" aria-label="Search posts"></label><select id="category-filter" class="filter-select" aria-label="Filter by category"><option>All</option>${CATEGORIES.map((category) => `<option ${state.category === category ? "selected" : ""}>${category}</option>`).join("")}</select></div>
    <div class="tab-pills" role="group" aria-label="Filter post type">${[["all", "Everything"], ["lost", "Lost"], ["found", "Found"]].map(([value, label]) => `<button class="tab-pill ${state.filter === value ? "active" : ""}" data-filter="${value}">${label}</button>`).join("")}</div>
    <div class="section-heading"><h2>${filtered.length} ${filtered.length === 1 ? "post" : "posts"}</h2></div>
    <div class="listing-list">${filtered.length ? filtered.map((item) => itemCard(item)).join("") : emptyState("⌕", "Nothing here just yet", "Try a different search, or be the first to post something.")}</div>`;
}

function renderMatches() {
  const matches = state.matches;
  const content = matches.length ? matches.map((match) => `
    <article class="match-card">
      <div class="match-card-top"><span>Could be connected to <strong>${escapeHtml(match.your_item.title)}</strong></span><span class="match-score">✳ ${match.score}% match</span></div>
      <div>${itemCard(match.item)}</div>
      ${match.reasons.length ? `<div class="match-reasons">${match.reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")}</div>` : ""}
      <div class="match-actions"><span class="your-item-note">AI suggestions are a starting point, not proof of ownership.</span>${match.item.has_contacted ? '<span class="status-pill accepted">Message sent</span>' : `<button class="button button-soft" data-action="contact" data-item-id="${match.item.id}">Contact poster</button>`}</div>
    </article>`).join("") : "";
  return `
    <div class="page-heading"><div><div class="greeting-label">A SECOND PAIR OF EYES</div><h1>Smart matches</h1><p>Similar details may connect your post with another campus report.</p></div></div>
    <div class="match-reasons" style="margin-bottom:18px"><span>Text similarity</span><span>Category</span><span>Campus location</span><span>Reported date</span></div>
    <div class="listing-list">${matches.length ? content : emptyState("✳", state.items.some((item) => Number(item.user_id) === Number(state.user.id)) ? "No likely matches yet" : "Start with a post", state.items.some((item) => Number(item.user_id) === Number(state.user.id)) ? "We'll compare your post with new reports as they come in." : "Post something lost or found and we'll look for matching details.", '<button class="button button-primary" data-action="new-post">＋ Post an item</button>')}</div>`;
}

function renderMyPosts() {
  const own = state.myItems;
  return `
    <div class="page-heading"><div><div class="greeting-label">YOUR ACTIVITY</div><h1>My reports</h1><p>Keep track of items you've reported to CUT Lost &amp; Found.</p></div><button class="button button-primary" data-action="new-post">＋ New report</button></div>
    <div class="listing-list">${own.length ? own.map((item) => `
      <article class="request-card"><div class="request-card-top"><div>${itemCard(item)}</div><span class="status-pill ${item.status === "resolved" ? "accepted" : ""}">${item.status === "resolved" ? "collection recorded" : "awaiting CUT management"}</span></div><div class="request-actions"><span class="your-item-note">${item.status === "resolved" ? "This report has been closed by an authorised manager." : "Only CUT management can record collection and close this report."}</span></div></article>`).join("") : emptyState("▤", "No posts from you yet", "When you post an item, it will show up here.", '<button class="button button-primary" data-action="new-post">＋ Report an item</button>')}</div>`;
}

function renderRequests() {
  const claims = state.claims;
  const content = claims.length ? claims.map((claim) => {
    const incoming = claim.direction === "incoming";
    return `<article class="request-card">
      <div class="request-card-top"><div><h3>${incoming ? "Message about your post" : "Your message"}</h3><span class="request-person">${incoming ? "From" : "To"} ${escapeHtml(claim.other_name)} · ${escapeHtml(claim.item_title)}</span></div><span class="status-pill ${claim.status}">${escapeHtml(claim.status)}</span></div>
      <p>“${escapeHtml(claim.message)}”</p>
      <div class="request-actions">${incoming && claim.status === "pending" ? `<button class="button button-soft" data-action="claim-status" data-claim-id="${claim.id}" data-status="accepted">Accept & share email</button><button class="button button-outline" data-action="claim-status" data-claim-id="${claim.id}" data-status="declined">Decline</button>` : ""}${claim.status === "accepted" ? `<a class="claim-contact" href="mailto:${encodeURIComponent(claim.other_email)}">✉ Email ${escapeHtml(claim.other_name)}</a>` : ""}</div>
    </article>`;
  }).join("") : "";
  return `
    <div class="page-heading"><div><div class="greeting-label">PRIVATE CONVERSATIONS</div><h1>Messages</h1><p>Arrange collection through CUT management; only staff can close a report.</p></div></div>
    <div class="request-list">${claims.length ? content : emptyState("↗", "No messages yet", "When someone reaches out about a post, the conversation will appear here.")}</div>`;
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium", timeStyle: "short",
  }).format(date);
}

function renderManagement() {
  const pending = state.items.filter((item) => item.status === "open");
  const records = state.handovers;
  return `
    <div class="page-heading"><div><div class="greeting-label">AUTHORISED CUT STAFF</div><h1>Collection register</h1><p>Record every recipient and collection time. Completed records are retained for review.</p></div></div>
    <section class="management-note"><span class="management-note-icon">▣</span><div><strong>Manager-controlled handover</strong><p>Only configured CUT managers can close a report. Every handover records the recipient's CUT ID and email, collection time, and the staff member recording it.</p></div></section>
    <section class="management-section"><div class="section-heading"><h2>Awaiting collection <span class="record-count">${pending.length}</span></h2></div>
      <div class="listing-list">${pending.length ? pending.map((item) => `<article class="request-card management-item"><div>${itemCard(item)}</div><button class="button button-primary" data-action="record-handover" data-item-id="${item.id}">Record collection</button></article>`).join("") : emptyState("✓", "All caught up", "There are no open CUT item reports awaiting collection.")}</div>
    </section>
    <section class="management-section"><div class="section-heading"><h2>Handover history <span class="record-count">${records.length}</span></h2></div>
      ${records.length ? `<div class="table-wrap"><table class="handover-table"><thead><tr><th>Register #</th><th>Item</th><th>Recipient CUT ID</th><th>Recipient email</th><th>Collected</th><th>Recorded by</th><th>Record entered</th></tr></thead><tbody>${records.map((record) => `<tr><td>${escapeHtml(record.id)}</td><td><strong>${escapeHtml(record.item_title)}</strong><span class="table-sub">Report #${escapeHtml(record.item_id)} · ${escapeHtml(record.item_kind)} · ${escapeHtml(record.location)}</span></td><td>${escapeHtml(record.recipient_id)}</td><td>${escapeHtml(record.recipient_email)}</td><td>${escapeHtml(formatDateTime(record.collected_at))}</td><td>${escapeHtml(record.recorded_by_name)}<span class="table-sub">${escapeHtml(record.recorded_by_email)}</span></td><td>${escapeHtml(formatDateTime(record.recorded_at))}</td></tr>`).join("")}</tbody></table></div>` : emptyState("▤", "No collections recorded yet", "Completed item handovers will remain listed here for future review.")}</section>`;
}

function renderView() {
  if (!state.user) return;
  const container = $("#view-container");
  if (state.view === "discover") container.innerHTML = renderDashboard();
  else if (state.view === "browse") container.innerHTML = renderDiscover();
  else if (state.view === "matches") container.innerHTML = renderMatches();
  else if (state.view === "my-posts") container.innerHTML = renderMyPosts();
  else if (state.view === "management" && state.user.is_manager) container.innerHTML = renderManagement();
  else container.innerHTML = renderRequests();
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === (state.view === "browse" ? "discover" : state.view);
    button.classList.toggle("active", active);
  });
}

function navigate(view) {
  state.view = view;
  renderView();
  $("#view-container").focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("show");
  clearTimeout(showToast.timeout);
  showToast.timeout = setTimeout(() => toast.classList.remove("show"), 3200);
}

function openModal(content) {
  $("#modal-root").innerHTML = `<div class="modal-backdrop" data-action="backdrop"><section class="modal" role="dialog" aria-modal="true">${content}</section></div>`;
  $(".modal").addEventListener("click", (event) => event.stopPropagation());
  $(".modal-close").focus();
}

function closeModal() {
  $("#modal-root").innerHTML = "";
}

function openPostModal() {
  openModal(`
    <div class="modal-head"><div><h2>Report an item</h2><p>Help the CUT community identify and return it.</p></div><button class="modal-close" data-action="close-modal" aria-label="Close">×</button></div>
    <form id="post-form">
      <div class="segmented" role="group" aria-label="Post type"><button type="button" class="active" data-kind="lost">I lost something</button><button type="button" data-kind="found">I found something</button></div>
      <input id="post-kind" type="hidden" name="kind" value="lost">
      <label class="field">What is it called?<input name="title" placeholder="e.g. Blue water bottle" minlength="3" maxlength="100" required></label>
      <label class="field">A few details<textarea name="description" placeholder="Color, brand, initials, or anything that helps identify it…" minlength="10" maxlength="1000" required></textarea></label>
      <div class="form-row"><label class="field">Category<select name="category" required>${CATEGORIES.map((category) => `<option>${category}</option>`).join("")}</select></label><label class="field">When?<input name="happened_on" type="date" max="${new Date().toISOString().slice(0, 10)}" value="${new Date().toISOString().slice(0, 10)}" required></label></div>
      <label class="field">Where on campus?<input name="location" placeholder="e.g. Library, second floor" minlength="2" maxlength="120" required></label>
      <p id="post-error" class="form-error" role="alert"></p>
      <div class="modal-footer"><button type="button" class="button button-outline" data-action="close-modal">Cancel</button><button class="button button-primary" type="submit">Publish post <span>→</span></button></div>
    </form>`);
}

function openContactModal(itemId) {
  const item = state.items.find((post) => Number(post.id) === Number(itemId)) || state.matches.find((match) => Number(match.item.id) === Number(itemId))?.item;
  if (!item) return;
  openModal(`
    <div class="modal-head"><div><h2>Reach out to ${escapeHtml(item.owner_name)}</h2><p>Your email stays private until they accept.</p></div><button class="modal-close" data-action="close-modal" aria-label="Close">×</button></div>
    <p style="font-size:12px;color:#68756d">About: <strong>${escapeHtml(item.title)}</strong></p>
    <form id="contact-form" data-item-id="${item.id}">
      <label class="field">Your message<textarea name="message" minlength="10" maxlength="500" placeholder="Share a detail that helps identify the item or arrange a safe handoff…" required></textarea></label>
      <p id="contact-error" class="form-error" role="alert"></p>
      <div class="modal-footer"><button type="button" class="button button-outline" data-action="close-modal">Cancel</button><button class="button button-primary" type="submit">Send privately <span>→</span></button></div>
    </form>`);
}

function openHandoverModal(itemId) {
  const item = state.items.find((post) => Number(post.id) === Number(itemId));
  if (!item || !state.user.is_manager) return;
  const now = new Date();
  const localNow = new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
    .toISOString().slice(0, 16);
  openModal(`
    <div class="modal-head"><div><h2>Record item collection</h2><p>This permanent CUT register entry closes the report.</p></div><button class="modal-close" data-action="close-modal" aria-label="Close">×</button></div>
    <div class="handover-item-summary"><span class="item-illustration" data-category="${escapeHtml(item.category)}">${CATEGORY_ICONS[item.category] || "✳"}</span><div><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.kind)} · ${escapeHtml(item.location)}</span></div></div>
    <form id="handover-form" data-item-id="${item.id}">
      <label class="field">Recipient CUT student/staff ID<input name="recipient_id" minlength="2" maxlength="40" autocomplete="off" placeholder="Enter the recipient's CUT ID" required></label>
      <label class="field">Recipient CUT email address<input name="recipient_email" type="email" maxlength="254" autocomplete="off" placeholder="recipient@cut.ac.zw" required></label>
      <label class="field">Collection date and time<input name="collected_at" type="datetime-local" max="${localNow}" value="${localNow}" required></label>
      <p class="audit-disclosure">Recorded by ${escapeHtml(state.user.name)} (${escapeHtml(state.user.email)}). Collection records are retained for future management review.</p>
      <p id="handover-error" class="form-error" role="alert"></p>
      <div class="modal-footer"><button type="button" class="button button-outline" data-action="close-modal">Cancel</button><button class="button button-primary" type="submit">Save collection record <span>→</span></button></div>
    </form>`);
}

async function refreshAndRender() {
  await loadData();
  renderView();
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button, [data-action='backdrop']");
  if (!button) return;
  if (button.dataset.view) return navigate(button.dataset.view);
  if (button.dataset.filter) {
    state.filter = button.dataset.filter;
    return renderView();
  }
  if (button.dataset.kind) {
    $("#post-kind").value = button.dataset.kind;
    document.querySelectorAll("[data-kind]").forEach((option) => option.classList.toggle("active", option === button));
    return;
  }
  const action = button.dataset.action;
  if (action === "new-post") return openPostModal();
  if (action === "browse") return navigate("browse");
  if (action === "close-modal" || action === "backdrop") return closeModal();
  if (action === "contact") return openContactModal(button.dataset.itemId);
  if (action === "record-handover") return openHandoverModal(button.dataset.itemId);
  if (action === "claim-status") {
    try {
      await api(`/api/claims/${button.dataset.claimId}`, { method: "PATCH", body: JSON.stringify({ status: button.dataset.status }) });
      await refreshAndRender();
      showToast(button.dataset.status === "accepted" ? "Accepted. You can now connect by email." : "Message declined.");
    } catch (error) { showToast(error.message, true); }
  }
});

document.addEventListener("submit", async (event) => {
  if (event.target.id === "auth-form") return;
  if (event.target.id === "post-form") {
    event.preventDefault();
    const form = event.target;
    const payload = Object.fromEntries(new FormData(form));
    try {
      await api("/api/items", { method: "POST", body: JSON.stringify(payload) });
      closeModal();
      await refreshAndRender();
      showToast("Your post is live. We'll look for possible matches.");
    } catch (error) { $("#post-error").textContent = error.message; }
  }
  if (event.target.id === "contact-form") {
    event.preventDefault();
    const form = event.target;
    const payload = Object.fromEntries(new FormData(form));
    try {
      await api(`/api/items/${form.dataset.itemId}/claims`, { method: "POST", body: JSON.stringify(payload) });
      closeModal();
      await refreshAndRender();
      showToast("Message sent privately to the poster.");
    } catch (error) { $("#contact-error").textContent = error.message; }
  }
  if (event.target.id === "handover-form") {
    event.preventDefault();
    const form = event.target;
    const payload = Object.fromEntries(new FormData(form));
    try {
      payload.collected_at = new Date(payload.collected_at).toISOString();
      await api("/api/management/handovers", {
        method: "POST",
        body: JSON.stringify({ ...payload, item_id: Number(form.dataset.itemId) }),
      });
      closeModal();
      await refreshAndRender();
      showToast("Collection recorded in the CUT handover register.");
    } catch (error) {
      $("#handover-error").textContent = error.message;
    }
  }
});

document.addEventListener("input", (event) => {
  if (event.target.id === "item-search") {
    state.search = event.target.value;
    const cursor = event.target.selectionStart;
    renderView();
    const input = $("#item-search");
    input.focus();
    input.setSelectionRange(cursor, cursor);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.id === "category-filter") {
    state.category = event.target.value;
    renderView();
  }
});

$("#auth-form").addEventListener("submit", handleAuth);
$("#auth-toggle").addEventListener("click", () => setAuthMode(state.authMode === "login" ? "register" : "login"));
$("#new-post-top").addEventListener("click", openPostModal);
$("#signout-button").addEventListener("click", async () => {
  let revokeError;
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (error) {
    revokeError = error;
  }
  state.token = null;
  state.user = null;
  sessionStorage.removeItem("campus-find-token");
  showAuth();
  if (revokeError) showToast("Signed out on this device, but the server could not revoke the session.", true);
});

async function start() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch((error) => console.error("Offline shell registration failed:", error));
  }
  if (!state.token) return showAuth();
  try {
    await loadData();
    showApp();
  } catch (error) {
    showAuth();
    $("#auth-error").textContent = error.message;
  }
}

start();
