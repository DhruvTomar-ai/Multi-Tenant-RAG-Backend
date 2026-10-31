import {
  buildUrl,
  createConversation,
  createTenant,
  getApiBaseUrl,
  getConversationMessages,
  listConversations,
  listDocuments,
  listTenants,
  searchTenant,
  sendMessage,
  setApiBaseUrl,
  uploadDocument,
} from './api.js';

const state = {
  tenants: [],
  documents: {},
  conversations: {},
  activeConversationId: null,
  activeTenantId: null,
};

const ui = {
  apiBaseUrl: document.getElementById('apiBaseUrl'),
  saveApiBaseUrl: document.getElementById('saveApiBaseUrl'),
  statusBadge: document.getElementById('appStatus'),
  tenantForm: document.getElementById('tenantForm'),
  tenantList: document.getElementById('tenantList'),
  documentForm: document.getElementById('documentForm'),
  documentTenantSelect: document.getElementById('documentTenantSelect'),
  documentFileInput: document.getElementById('documentFileInput'),
  documentMetadata: document.getElementById('documentMetadata'),
  documentList: document.getElementById('documentList'),
  searchForm: document.getElementById('searchForm'),
  searchTenantSelect: document.getElementById('searchTenantSelect'),
  searchQuery: document.getElementById('searchQuery'),
  searchK: document.getElementById('searchK'),
  searchThreshold: document.getElementById('searchThreshold'),
  searchResults: document.getElementById('searchResults'),
  conversationForm: document.getElementById('conversationForm'),
  conversationTenantSelect: document.getElementById('conversationTenantSelect'),
  conversationTitle: document.getElementById('conversationTitle'),
  conversationList: document.getElementById('conversationList'),
  chatThread: document.getElementById('chatThread'),
  messageForm: document.getElementById('messageForm'),
  messageInput: document.getElementById('messageInput'),
};

function setStatus(message, variant = 'neutral') {
  ui.statusBadge.textContent = message;
  ui.statusBadge.className = `status-badge ${variant}`;
}

function toggleSection(targetId) {
  document.querySelectorAll('.panel').forEach((panel) => {
    panel.classList.toggle('active-panel', panel.id === targetId);
  });

  document.querySelectorAll('.nav-link').forEach((link) => {
    link.classList.toggle('active', link.dataset.target === targetId);
  });
}

function renderTenantCards() {
  if (!state.tenants.length) {
    ui.tenantList.innerHTML = '<div class="empty-state">No tenants found.</div>';
    return;
  }

  ui.tenantList.innerHTML = state.tenants.map((tenant) => `
    <div class="tenant-card" data-id="${tenant.id}">
      <h4>${tenant.name}</h4>
      <div class="meta-line">
        <span>ID: ${tenant.id}</span>
        <span>Slug: ${tenant.slug}</span>
      </div>
      <div class="meta-line">
        <span class="severity ${tenant.is_active ? 'success' : 'info'}">${tenant.is_active ? 'Active' : 'Inactive'}</span>
      </div>
    </div>
  `).join('');
}

function renderDocumentCards() {
  const tenantId = state.activeTenantId || getSelectedValue(ui.documentTenantSelect);
  const documents = state.documents[tenantId] || [];

  if (!documents.length) {
    ui.documentList.innerHTML = '<div class="empty-state">No documents uploaded for this tenant yet.</div>';
    return;
  }

  ui.documentList.innerHTML = documents.map((doc) => `
    <div class="document-card">
      <h4>${doc.filename}</h4>
      <div class="meta-line">
        <span>ID: ${doc.id}</span>
        <span>Status: ${doc.status}</span>
        <span>MIME: ${doc.mime_type || 'n/a'}</span>
      </div>
      <div class="meta-line">
        <span>Storage: ${doc.storage_key}</span>
      </div>
      <div class="context-block">
        <strong>Metadata:</strong> ${doc.metadata ? JSON.stringify(doc.metadata) : 'n/a'}
      </div>
    </div>
  `).join('');
}

function renderSearchResults(items) {
  if (!items || !items.length) {
    ui.searchResults.innerHTML = '<div class="empty-state">No results found for this query.</div>';
    return;
  }

  ui.searchResults.innerHTML = items.map((item) => `
    <div class="search-result">
      <h4>Chunk #${item.chunk_index}</h4>
      <div class="meta-line">
        <span>Document: ${item.document_id}</span>
        <span>Chunk: ${item.chunk_id}</span>
        <span>Similarity: ${Number(item.similarity).toFixed(4)}</span>
      </div>
      <div class="context-block">${item.content}</div>
    </div>
  `).join('');
}

function renderConversations() {
  const tenantId = getSelectedValue(ui.conversationTenantSelect);
  const conversations = state.conversations[tenantId] || [];

  if (!conversations.length) {
    ui.conversationList.innerHTML = '<div class="empty-state">No conversations created for this tenant yet.</div>';
    return;
  }

  ui.conversationList.innerHTML = conversations.map((conversation) => `
    <div class="conversation-item" data-conversation-id="${conversation.id}">
      <h4>${conversation.title || 'Untitled conversation'}</h4>
      <div class="meta-line">
        <span>ID: ${conversation.id}</span>
        <span>User: ${conversation.user_id}</span>
      </div>
      <button class="secondary small" data-open-conversation="${conversation.id}">Open</button>
    </div>
  `).join('');

  document.querySelectorAll('[data-open-conversation]').forEach((button) => {
    button.addEventListener('click', async () => {
      const conversationId = Number(button.dataset.openConversation);
      state.activeConversationId = conversationId;
      await loadConversationThread(tenantId, conversationId);
    });
  });
}

function renderChatThread(messages = []) {
  if (!messages.length) {
    ui.chatThread.innerHTML = '<div class="empty-state">No chat history yet. Ask a question to start.</div>';
    return;
  }

  ui.chatThread.innerHTML = messages.map((message) => `
    <div class="chat-bubble ${message.role === 'user' ? 'user' : 'assistant'}">
      <div class="role">${message.role}</div>
      <div>${message.content}</div>
    </div>
  `).join('');
}

function getSelectedValue(selectEl) {
  return selectEl.value ? Number(selectEl.value) : null;
}

function populateTenantSelects() {
  const options = state.tenants.map((tenant) => `<option value="${tenant.id}">${tenant.name} (${tenant.id})</option>`).join('');
  ui.documentTenantSelect.innerHTML = options;
  ui.searchTenantSelect.innerHTML = options;
  ui.conversationTenantSelect.innerHTML = options;

  if (state.tenants.length) {
    const tenantId = state.tenants[0].id;
    state.activeTenantId = tenantId;
    ui.documentTenantSelect.value = String(tenantId);
    ui.searchTenantSelect.value = String(tenantId);
    ui.conversationTenantSelect.value = String(tenantId);
    loadDocumentsForTenant(tenantId);
    loadConversationsForTenant(tenantId);
  }
}

async function loadTenants() {
  try {
    setStatus('Loading tenants...', 'warning');
    const tenants = await listTenants();
    state.tenants = tenants;
    renderTenantCards();
    populateTenantSelects();
    setStatus('Tenants loaded', 'success');
  } catch (error) {
    setStatus(error.message || 'Failed to load tenants', 'error');
  }
}

async function loadDocumentsForTenant(tenantId) {
  try {
    const documents = await listDocuments(tenantId);
    state.documents[tenantId] = documents;
    renderDocumentCards();
  } catch (error) {
    setStatus(error.message || 'Unable to load documents', 'error');
  }
}

async function loadConversationsForTenant(tenantId) {
  try {
    const conversations = await listConversations(tenantId);
    state.conversations[tenantId] = conversations;
    renderConversations();
    if (conversations.length && !state.activeConversationId) {
      state.activeConversationId = conversations[0].id;
      await loadConversationThread(tenantId, state.activeConversationId);
    }
  } catch (error) {
    setStatus(error.message || 'Unable to load conversations', 'error');
  }
}

async function loadConversationThread(tenantId, conversationId) {
  try {
    const messages = await getConversationMessages(tenantId, conversationId);
    renderChatThread(messages);
  } catch (error) {
    setStatus(error.message || 'Could not load conversation', 'error');
  }
}

ui.saveApiBaseUrl.addEventListener('click', () => {
  const base = setApiBaseUrl(ui.apiBaseUrl.value);
  ui.apiBaseUrl.value = base;
  setStatus(`API base updated: ${base}`, 'success');
  loadTenants();
});

ui.apiBaseUrl.value = getApiBaseUrl();

document.querySelectorAll('.nav-link').forEach((button) => {
  button.addEventListener('click', () => toggleSection(button.dataset.target));
});

ui.tenantForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const formData = new FormData(ui.tenantForm);
  const payload = Object.fromEntries(formData.entries());

  try {
    setStatus('Creating tenant...', 'warning');
    await createTenant(payload);
    ui.tenantForm.reset();
    await loadTenants();
    setStatus('Tenant created', 'success');
  } catch (error) {
    setStatus(error.message || 'Tenant creation failed', 'error');
  }
});

ui.documentForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const tenantId = getSelectedValue(ui.documentTenantSelect);
  const file = ui.documentFileInput.files[0];

  if (!tenantId || !file) {
    setStatus('Please choose a tenant and a file', 'warning');
    return;
  }

  try {
    setStatus('Uploading document...', 'warning');
    let metadata = null;
    const metadataText = ui.documentMetadata.value.trim();
    if (metadataText) {
      try {
        metadata = JSON.parse(metadataText);
      } catch {
        setStatus('Metadata must be valid JSON', 'error');
        return;
      }
    }

    await uploadDocument(tenantId, file, metadata);
    ui.documentForm.reset();
    await loadDocumentsForTenant(tenantId);
    setStatus('Document uploaded', 'success');
  } catch (error) {
    setStatus(error.message || 'Upload failed', 'error');
  }
});

ui.searchForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const tenantId = getSelectedValue(ui.searchTenantSelect);
  const query = ui.searchQuery.value.trim();
  const k = Number(ui.searchK.value || 5);
  const threshold = Number(ui.searchThreshold.value || 0);

  if (!tenantId || !query) {
    setStatus('Please choose a tenant and enter a query', 'warning');
    return;
  }

  try {
    setStatus('Searching...', 'warning');
    const result = await searchTenant(tenantId, query, k, threshold);
    renderSearchResults(result.results || []);
    setStatus('Search complete', 'success');
  } catch (error) {
    setStatus(error.message || 'Search failed', 'error');
  }
});

ui.conversationForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const tenantId = getSelectedValue(ui.conversationTenantSelect);
  const title = ui.conversationTitle.value.trim();

  if (!tenantId) {
    setStatus('Please choose a tenant', 'warning');
    return;
  }

  try {
    setStatus('Creating conversation...', 'warning');
    const conversation = await createConversation(tenantId, title || 'New conversation');
    state.activeConversationId = conversation.id;
    ui.conversationTitle.value = '';
    await loadConversationsForTenant(tenantId);
    await loadConversationThread(tenantId, conversation.id);
    setStatus('Conversation created', 'success');
  } catch (error) {
    setStatus(error.message || 'Conversation creation failed', 'error');
  }
});

ui.messageForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const tenantId = getSelectedValue(ui.conversationTenantSelect);
  const message = ui.messageInput.value.trim();

  if (!tenantId || !state.activeConversationId || !message) {
    setStatus('Select a tenant, open a conversation, and enter a message', 'warning');
    return;
  }

  try {
    setStatus('Sending message...', 'warning');
    const response = await sendMessage(tenantId, state.activeConversationId, {
      user_id: 1,
      content: message,
      k: 3,
      score_threshold: 0.0,
    });

    const messages = await getConversationMessages(tenantId, state.activeConversationId);
    renderChatThread(messages);
    ui.messageInput.value = '';
    setStatus('Message sent', 'success');
  } catch (error) {
    setStatus(error.message || 'Message sending failed', 'error');
  }
});

ui.documentTenantSelect.addEventListener('change', (event) => {
  const tenantId = Number(event.target.value);
  state.activeTenantId = tenantId;
  loadDocumentsForTenant(tenantId);
});

ui.conversationTenantSelect.addEventListener('change', async (event) => {
  const tenantId = Number(event.target.value);
  state.activeTenantId = tenantId;
  state.activeConversationId = null;
  await loadConversationsForTenant(tenantId);
  const convs = state.conversations[tenantId] || [];
  if (convs.length) {
    state.activeConversationId = convs[0].id;
    await loadConversationThread(tenantId, convs[0].id);
  }
});

document.getElementById('refreshTenantsBtn').addEventListener('click', loadTenants);
document.getElementById('refreshDocumentsBtn').addEventListener('click', () => {
  const tenantId = getSelectedValue(ui.documentTenantSelect);
  if (tenantId) {
    loadDocumentsForTenant(tenantId);
  }
});
document.getElementById('refreshConversationsBtn').addEventListener('click', () => {
  const tenantId = getSelectedValue(ui.conversationTenantSelect);
  if (tenantId) {
    loadConversationsForTenant(tenantId);
  }
});

loadTenants();
