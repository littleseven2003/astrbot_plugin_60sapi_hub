const bridge = window.AstrBotPluginView;

const elements = {
  status: document.getElementById("status"),
  siteCount: document.getElementById("site-count"),
  sitesList: document.getElementById("sites-list"),
  listState: document.getElementById("list-state"),
  listStateText: document.getElementById("list-state-text"),
  retryButton: document.getElementById("retry-button"),
  emptyState: document.getElementById("empty-state"),
  refreshButton: document.getElementById("refresh-button"),
  previewSite: document.getElementById("preview-site"),
  previewContentType: document.getElementById("preview-content-type"),
  previewButton: document.getElementById("preview-button"),
  previewHint: document.getElementById("preview-hint"),
  previewStatus: document.getElementById("preview-status"),
  previewResult: document.getElementById("preview-result"),
  previewSource: document.getElementById("preview-source"),
  previewContentTypeLabel: document.getElementById("preview-content-type-label"),
  previewEndpoint: document.getElementById("preview-endpoint"),
  previewDateItem: document.getElementById("preview-date-item"),
  previewContentDate: document.getElementById("preview-content-date"),
  previewItemCount: document.getElementById("preview-item-count"),
  previewFetchedAt: document.getElementById("preview-fetched-at"),
  previewWarnings: document.getElementById("preview-warnings"),
  previewEmpty: document.getElementById("preview-empty"),
  previewEmptyText: document.getElementById("preview-empty-text"),
  previewNewsList: document.getElementById("preview-news-list"),
  previewBiliList: document.getElementById("preview-bili-list"),
  previewTip: document.getElementById("preview-tip"),
  previewSourceLink: document.getElementById("preview-source-link"),
  createButton: document.getElementById("create-button"),
  emptyCreateButton: document.getElementById("empty-create-button"),
  siteDialog: document.getElementById("site-dialog"),
  siteForm: document.getElementById("site-form"),
  dialogTitle: document.getElementById("dialog-title"),
  name: document.getElementById("name"),
  timeout: document.getElementById("timeout"),
  baseUrl: document.getElementById("base-url"),
  enabled: document.getElementById("enabled"),
  credentialState: document.getElementById("credential-state"),
  credentialActions: document.getElementById("credential-actions"),
  tokenField: document.getElementById("token-field"),
  tokenLabel: document.getElementById("token-label"),
  tokenHelp: document.getElementById("token-help"),
  bearerToken: document.getElementById("bearer-token"),
  formError: document.getElementById("form-error"),
  writeUncertain: document.getElementById("write-uncertain"),
  writeUncertainText: document.getElementById("write-uncertain-text"),
  recheckWriteButton: document.getElementById("recheck-write-button"),
  reviewCloseButton: document.getElementById("review-close-button"),
  closeDialogButton: document.getElementById("close-dialog-button"),
  cancelButton: document.getElementById("cancel-button"),
  saveButton: document.getElementById("save-button"),
  deleteDialog: document.getElementById("delete-dialog"),
  deleteMessage: document.getElementById("delete-message"),
  deleteError: document.getElementById("delete-error"),
  deleteUncertain: document.getElementById("delete-uncertain"),
  deleteUncertainText: document.getElementById("delete-uncertain-text"),
  recheckDeleteButton: document.getElementById("recheck-delete-button"),
  deleteReviewCloseButton: document.getElementById(
    "delete-review-close-button",
  ),
  deleteCancelButton: document.getElementById("delete-cancel-button"),
  deleteConfirmButton: document.getElementById("delete-confirm-button"),
};

const state = {
  sites: [],
  mode: "create",
  editingId: null,
  editingRevision: null,
  deleteTarget: null,
  loading: false,
  saving: false,
  deleting: false,
  pendingWrite: null,
  previewLoading: false,
  previewSiteId: null,
  previewRequestId: 0,
};

const CONTENT_LABELS = {
  daily_news: "每日新闻",
  bilibili_hot: "Bilibili 热搜",
};

const EXPLICIT_WRITE_REJECTIONS = [
  /无效[:：]/,
  /请求体必须是 JSON 对象/,
  /需要登录后才能管理站点/,
  /missing api key/i,
  /unauthorized/i,
  /not authenticated/i,
  /forbidden/i,
  /插件当前未启用/,
  /站点不存在或已被删除/,
  /已被其他操作修改/,
  /站点存储暂不可用/,
];

function showStatus(message, tone = "info") {
  elements.status.textContent = message;
  elements.status.dataset.tone = tone;
  elements.status.hidden = false;
}

function clearStatus() {
  elements.status.textContent = "";
  elements.status.hidden = true;
  delete elements.status.dataset.tone;
}

function showFormError(message) {
  elements.formError.textContent = message;
  elements.formError.hidden = false;
}

function clearFormError() {
  elements.formError.textContent = "";
  elements.formError.hidden = true;
}

function setListState(message, showRetry) {
  elements.listStateText.textContent = message;
  elements.retryButton.hidden = !showRetry;
  elements.listState.hidden = false;
}

function clearListState() {
  elements.listStateText.textContent = "";
  elements.retryButton.hidden = true;
  elements.listState.hidden = true;
}

function isTransportErrorMessage(message) {
  return (
    /^(failed to fetch|load failed|network error|request aborted)$/i.test(
      message,
    ) ||
    /timeout of \d+ms exceeded/i.test(message) ||
    /\b(ECONNABORTED|ECONNRESET|ERR_NETWORK|ERR_EMPTY_RESPONSE)\b/i.test(
      message,
    ) ||
    /net::err_/i.test(message)
  );
}

function friendlyErrorMessage(error) {
  const message =
    error && typeof error.message === "string" ? error.message.trim() : "";
  if (!message) {
    return "操作失败，请稍后重试。";
  }
  if (isTransportErrorMessage(message)) {
    return "无法连接后台，请检查网络后重试。";
  }
  return message;
}

function isConflictMessage(message) {
  return message.includes("已被其他操作修改");
}

function isMissingMessage(message) {
  return message.includes("不存在") || message.includes("已被删除");
}

function isExplicitWriteRejection(message) {
  return EXPLICIT_WRITE_REJECTIONS.some((pattern) =>
    pattern.test(message),
  );
}

function writeFailureDetail(error) {
  const message =
    error && typeof error.message === "string" ? error.message.trim() : "";
  if (!message) {
    return "未收到可用的错误信息。";
  }
  if (isTransportErrorMessage(message)) {
    return "无法连接后台或未收到服务器响应。";
  }
  return message;
}

function selectedCredentialAction() {
  const selected = elements.siteForm.querySelector(
    'input[name="credential_action"]:checked',
  );
  return selected ? selected.value : "keep";
}

function setFormDisabled(disabled) {
  for (const control of elements.siteForm.querySelectorAll(
    "input, button",
  )) {
    control.disabled = disabled;
  }
  elements.siteForm.setAttribute("aria-busy", String(disabled));
}

function openDialog(dialog) {
  if (typeof dialog.showModal === "function") {
    if (!dialog.open) {
      dialog.showModal();
    }
  } else {
    dialog.setAttribute("open", "");
  }
}

function closeDialog(dialog) {
  if (typeof dialog.close === "function") {
    if (dialog.open) {
      dialog.close();
    }
  } else {
    dialog.removeAttribute("open");
  }
}

function normalizeSite(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const id = typeof raw.id === "string" ? raw.id : "";
  if (!id) {
    return null;
  }
  return {
    id,
    name: typeof raw.name === "string" ? raw.name : "未命名站点",
    base_url: typeof raw.base_url === "string" ? raw.base_url : "",
    enabled: raw.enabled === true,
    timeout_seconds: Number.isFinite(Number(raw.timeout_seconds))
      ? Number(raw.timeout_seconds)
      : 10,
    has_bearer_token: raw.has_bearer_token === true,
    revision: Number.isInteger(Number(raw.revision))
      ? Number(raw.revision)
      : 1,
    updated_at:
      typeof raw.updated_at === "string" ? raw.updated_at : "",
  };
}

function formatTimeout(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "—";
  }
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

function formatUpdatedAt(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function makeButton(label, className, onClick, ariaLabel) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  if (ariaLabel) {
    button.setAttribute("aria-label", ariaLabel);
  }
  button.addEventListener("click", onClick);
  return button;
}

function addMeta(container, label, value) {
  const item = document.createElement("span");
  const name = document.createElement("strong");
  name.textContent = `${label} `;
  item.append(name, document.createTextNode(value));
  container.append(item);
}

function renderSite(site) {
  const row = document.createElement("li");
  row.className = "site-row";
  row.dataset.enabled = String(site.enabled);

  const main = document.createElement("div");
  main.className = "site-main";

  const titleLine = document.createElement("div");
  titleLine.className = "site-title-line";
  const name = document.createElement("h3");
  name.className = "site-name";
  name.textContent = site.name;
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = site.enabled ? "已启用" : "已停用";
  titleLine.append(name, badge);

  const url = document.createElement("p");
  url.className = "site-url";
  url.textContent = site.base_url;

  const meta = document.createElement("p");
  meta.className = "site-meta";
  addMeta(meta, "超时", `${formatTimeout(site.timeout_seconds)} 秒`);
  addMeta(meta, "凭据", site.has_bearer_token ? "已设置" : "未设置");
  const updated = formatUpdatedAt(site.updated_at);
  if (updated) {
    addMeta(meta, "最近更新", updated);
  }

  main.append(titleLine, url, meta);

  const actions = document.createElement("div");
  actions.className = "site-actions";
  actions.append(
    makeButton(
      "编辑",
      "button button-secondary",
      () => openEdit(site),
      `编辑站点 ${site.name}`,
    ),
    makeButton(
      "删除",
      "button button-danger",
      () => openDelete(site),
      `删除站点 ${site.name}`,
    ),
  );

  row.append(main, actions);
  return row;
}

function renderSites() {
  elements.siteCount.textContent = `${state.sites.length} 个`;
  elements.sitesList.replaceChildren();

  if (state.sites.length === 0) {
    elements.sitesList.hidden = true;
    elements.emptyState.hidden = false;
    clearListState();
    return;
  }

  elements.emptyState.hidden = true;
  elements.sitesList.hidden = false;
  clearListState();
  for (const site of state.sites) {
    elements.sitesList.append(renderSite(site));
  }
}

async function loadSites() {
  if (!bridge || state.loading) {
    return false;
  }
  let succeeded = false;
  state.loading = true;
  elements.refreshButton.disabled = true;
  elements.sitesList.setAttribute("aria-busy", "true");
  if (state.sites.length === 0) {
    elements.emptyState.hidden = true;
    elements.sitesList.hidden = true;
    setListState("正在读取站点…", false);
  }

  try {
    const result = await bridge.apiGet("sites");
    const rawSites = result && Array.isArray(result.sites) ? result.sites : [];
    state.sites = rawSites.map(normalizeSite).filter(Boolean);
    renderSites();
    syncPreviewSites();
    succeeded = true;
    if (elements.status.dataset.tone === "error") {
      clearStatus();
    }
  } catch (error) {
    const message = friendlyErrorMessage(error);
    showStatus(`读取站点失败：${message}`, "error");
    if (state.sites.length === 0) {
      elements.emptyState.hidden = true;
      elements.sitesList.hidden = true;
      setListState("无法读取站点配置。请确认插件已启用，然后重试。", true);
    }
  } finally {
    state.loading = false;
    elements.refreshButton.disabled = false;
    elements.sitesList.setAttribute("aria-busy", "false");
  }
  return succeeded;
}

function showPreviewStatus(message, tone = "info") {
  elements.previewStatus.textContent = message;
  elements.previewStatus.dataset.tone = tone;
  elements.previewStatus.hidden = false;
}

function hidePreviewStatus() {
  elements.previewStatus.textContent = "";
  elements.previewStatus.hidden = true;
  delete elements.previewStatus.dataset.tone;
}

function clearPreviewResult() {
  elements.previewResult.hidden = true;
  elements.previewSource.textContent = "";
  elements.previewSource.removeAttribute("title");
  elements.previewContentTypeLabel.textContent = "";
  elements.previewEndpoint.textContent = "";
  elements.previewDateItem.hidden = false;
  elements.previewContentDate.textContent = "";
  elements.previewItemCount.textContent = "";
  elements.previewFetchedAt.textContent = "";
  elements.previewFetchedAt.removeAttribute("datetime");
  elements.previewWarnings.textContent = "";
  elements.previewWarnings.hidden = true;
  elements.previewEmpty.hidden = true;
  elements.previewEmptyText.textContent = "上游返回了合法的空内容。";
  elements.previewNewsList.replaceChildren();
  elements.previewNewsList.hidden = true;
  elements.previewBiliList.replaceChildren();
  elements.previewBiliList.hidden = true;
  elements.previewTip.textContent = "";
  elements.previewTip.hidden = true;
  elements.previewSourceLink.textContent = "";
  elements.previewSourceLink.hidden = true;
}

function resetPreviewResult() {
  state.previewRequestId += 1;
  clearPreviewResult();
  hidePreviewStatus();
}

function syncPreviewSites() {
  resetPreviewResult();
  const enabledSites = state.sites.filter((site) => site.enabled);
  if (!enabledSites.some((site) => site.id === state.previewSiteId)) {
    state.previewSiteId = enabledSites.length ? enabledSites[0].id : null;
  }

  elements.previewSite.replaceChildren();
  if (enabledSites.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "没有已启用的站点";
    elements.previewSite.append(option);
    elements.previewSite.disabled = true;
    elements.previewContentType.disabled = true;
    elements.previewButton.disabled = true;
    elements.previewHint.textContent =
      "暂无已启用站点。请先在站点列表中启用一个站点。";
    elements.previewHint.hidden = false;
    return;
  }

  for (const site of enabledSites) {
    const option = document.createElement("option");
    option.value = site.id;
    option.textContent = site.name;
    elements.previewSite.append(option);
  }
  elements.previewSite.value = state.previewSiteId;
  elements.previewSite.disabled = state.previewLoading;
  elements.previewContentType.disabled = state.previewLoading;
  elements.previewButton.disabled = state.previewLoading;
  elements.previewHint.hidden = true;
}

function setPreviewBusy(busy) {
  state.previewLoading = busy;
  const hasSite = Boolean(elements.previewSite.value);
  elements.previewSite.disabled = busy || !hasSite;
  elements.previewContentType.disabled = busy || !hasSite;
  elements.previewButton.disabled = busy || !hasSite;
  elements.previewButton.textContent = busy ? "正在获取…" : "获取内容";
  elements.previewSite.setAttribute("aria-busy", String(busy));
  elements.previewContentType.setAttribute("aria-busy", String(busy));
}

function renderPreview(snapshot) {
  if (!snapshot || typeof snapshot !== "object" || !Array.isArray(snapshot.items)) {
    throw new Error("预览数据格式异常。");
  }
  const contentType = snapshot.content_type;
  const label = CONTENT_LABELS[contentType];
  if (!label) {
    throw new Error("预览结果与当前选择不符。");
  }
  const isBili = contentType === "bilibili_hot";
  const sourceName =
    typeof snapshot.site_name === "string" && snapshot.site_name
      ? snapshot.site_name
      : "未知站点";
  const sourceId =
    typeof snapshot.site_id === "string" && snapshot.site_id
      ? snapshot.site_id
      : "未知 ID";
  elements.previewSource.textContent = sourceName;
  elements.previewSource.title = `${sourceName}（${sourceId}）`;
  elements.previewContentTypeLabel.textContent = label;
  elements.previewEndpoint.textContent =
    typeof snapshot.endpoint === "string" && snapshot.endpoint
      ? snapshot.endpoint
      : isBili
        ? "/v2/bili"
        : "/v2/60s";
  elements.previewDateItem.hidden = isBili;
  elements.previewContentDate.textContent = isBili
    ? ""
    : typeof snapshot.content_date === "string" && snapshot.content_date
      ? snapshot.content_date
      : "未知";
  elements.previewItemCount.textContent = `${snapshot.items.length} 条`;
  const fetchedAt =
    typeof snapshot.fetched_at === "string" ? snapshot.fetched_at : "";
  const displayFetchedAt = formatUpdatedAt(fetchedAt);
  elements.previewFetchedAt.textContent = displayFetchedAt || "未知";
  if (fetchedAt) {
    elements.previewFetchedAt.setAttribute("datetime", fetchedAt);
  }

  const warnings = [];
  if (!isBili) {
    const rawWarnings = Array.isArray(snapshot.warnings)
      ? snapshot.warnings.filter((item) => typeof item === "string" && item)
      : [];
    warnings.push(...rawWarnings);
    if (warnings.length === 0 && snapshot.date_is_unknown) {
      warnings.push("上游未提供可识别的内容日期，日期保持未知。");
    }
    if (warnings.length === 0 && snapshot.is_stale) {
      warnings.push("上游内容日期早于当前日期；未用今天覆盖。");
    }
  }
  elements.previewWarnings.textContent = warnings.join("\n");
  elements.previewWarnings.hidden = warnings.length === 0;

  const list = isBili ? elements.previewBiliList : elements.previewNewsList;
  const otherList = isBili ? elements.previewNewsList : elements.previewBiliList;
  otherList.replaceChildren();
  otherList.hidden = true;
  list.replaceChildren();
  for (const item of snapshot.items) {
    if (
      !item ||
      typeof item !== "object" ||
      typeof item.title !== "string" ||
      !item.title.trim()
    ) {
      throw new Error("预览数据格式异常。");
    }
    const row = document.createElement("li");
    const title = document.createElement("p");
    title.className = "news-title";
    title.textContent = item.title;
    row.append(title);
    if (typeof item.link === "string" && item.link) {
      const link = document.createElement("p");
      link.className = "news-link";
      link.textContent = `链接：${item.link}`;
      row.append(link);
    }
    list.append(row);
  }
  const empty = snapshot.items.length === 0;
  list.hidden = empty;
  elements.previewEmpty.hidden = !empty;
  elements.previewEmptyText.textContent = isBili
    ? "上游返回了合法的空 Bilibili 热搜列表（0 条）。"
    : "上游返回了合法的空内容（0 条新闻）。";

  if (!isBili && typeof snapshot.tip === "string" && snapshot.tip.trim()) {
    elements.previewTip.textContent = `微语：${snapshot.tip}`;
    elements.previewTip.hidden = false;
  }
  if (
    !isBili &&
    typeof snapshot.source_link === "string" &&
    snapshot.source_link
  ) {
    elements.previewSourceLink.textContent =
      `来源链接：${snapshot.source_link}`;
    elements.previewSourceLink.hidden = false;
  }

  elements.previewResult.hidden = false;
  showPreviewStatus(
    empty
      ? isBili
        ? "已获取，上游返回了合法的空 Bilibili 热搜列表。"
        : "已获取，上游返回了合法的空内容（0 条新闻）。"
      : `已获取：${label} ${snapshot.items.length} 条。`,
    "success",
  );
}

async function previewContent() {
  if (!bridge || state.previewLoading) {
    return;
  }
  const siteId = elements.previewSite.value;
  if (!siteId) {
    showPreviewStatus("请先选择已启用站点。", "error");
    return;
  }
  const contentType = elements.previewContentType.value;
  const label = CONTENT_LABELS[contentType];
  if (!label) {
    showPreviewStatus("请选择支持的内容类型。", "error");
    return;
  }

  const requestId = ++state.previewRequestId;
  state.previewLoading = true;
  clearPreviewResult();
  showPreviewStatus(`正在获取${label}…`, "info");
  setPreviewBusy(true);

  try {
    const snapshot = await bridge.apiPost(
      "content/preview",
      { site_id: siteId, content_type: contentType },
    );
    if (
      requestId !== state.previewRequestId ||
      elements.previewSite.value !== siteId ||
      elements.previewContentType.value !== contentType
    ) {
      return;
    }
    renderPreview(snapshot);
  } catch (error) {
    if (requestId === state.previewRequestId) {
      clearPreviewResult();
      showPreviewStatus(
        `获取失败：${friendlyErrorMessage(error)}`,
        "error",
      );
    }
  } finally {
    state.previewLoading = false;
    setPreviewBusy(false);
  }
}

function resetCredentialForm() {
  elements.bearerToken.value = "";
  elements.bearerToken.required = false;
  elements.tokenField.hidden = true;
  elements.credentialActions.hidden = true;
  elements.credentialState.hidden = true;
  elements.credentialState.textContent = "";
  const keep = elements.siteForm.querySelector(
    'input[name="credential_action"][value="keep"]',
  );
  if (keep) {
    keep.checked = true;
  }
}

function syncCredentialFields(hasToken, action) {
  if (state.mode === "create") {
    elements.credentialActions.hidden = true;
    elements.credentialState.hidden = true;
    elements.tokenField.hidden = false;
    elements.tokenLabel.textContent = "Bearer 凭据（可选）";
    elements.tokenHelp.textContent =
      "留空表示不设置凭据。只填写凭据本身，不要带 Bearer 前缀；保存后不会回显。";
    elements.bearerToken.required = false;
    return;
  }

  elements.credentialActions.hidden = false;
  elements.credentialState.hidden = false;
  if (action === "set") {
    elements.credentialState.textContent = hasToken
      ? "当前已设置凭据。保存后将用新值替换原凭据。"
      : "当前未设置凭据。保存后将写入新凭据。";
    elements.tokenField.hidden = false;
    elements.tokenLabel.textContent = "新 Bearer 凭据";
    elements.tokenHelp.textContent =
      "只填写凭据本身，不要带 Bearer 前缀；保存后不会回显。";
    elements.bearerToken.required = true;
  } else if (action === "clear") {
    elements.credentialState.textContent = "保存后将清除当前凭据。";
    elements.tokenField.hidden = true;
    elements.bearerToken.required = false;
  } else {
    elements.credentialState.textContent = hasToken
      ? "当前已设置凭据。默认保留原值，不会回显。"
      : "当前未设置凭据。默认保留现状。";
    elements.tokenField.hidden = true;
    elements.bearerToken.required = false;
  }
}

function pendingElements(pending) {
  if (pending.kind === "delete") {
    return {
      panel: elements.deleteUncertain,
      text: elements.deleteUncertainText,
      recheck: elements.recheckDeleteButton,
      review: elements.deleteReviewCloseButton,
    };
  }
  return {
    panel: elements.writeUncertain,
    text: elements.writeUncertainText,
    recheck: elements.recheckWriteButton,
    review: elements.reviewCloseButton,
  };
}

function updatePendingUI(text) {
  const pending = state.pendingWrite;
  if (!pending) {
    return;
  }
  const ui = pendingElements(pending);
  ui.panel.hidden = false;
  ui.text.textContent = text;

  if (pending.kind !== "delete") {
    setFormDisabled(true);
  } else {
    elements.deleteCancelButton.disabled = true;
    elements.deleteConfirmButton.disabled = true;
    elements.deleteConfirmButton.textContent = "等待确认结果";
  }

  if (pending.phase === "checking") {
    ui.recheck.hidden = false;
    ui.recheck.disabled = true;
    ui.recheck.textContent = "正在重新读取…";
    ui.review.hidden = true;
  } else if (pending.phase === "review") {
    ui.recheck.hidden = true;
    ui.review.hidden = false;
    ui.review.disabled = false;
  } else {
    ui.recheck.hidden = false;
    ui.recheck.disabled = false;
    ui.recheck.textContent = "重新读取最新列表";
    ui.review.hidden = true;
  }
}

function enterPendingWrite(kind, detail, target) {
  state.pendingWrite = {
    kind,
    phase: "unconfirmed",
    targetId: target ? target.id : null,
  };
  if (kind === "delete") {
    elements.deleteError.hidden = true;
    elements.deleteError.textContent = "";
  } else {
    clearFormError();
    elements.saveButton.disabled = true;
    elements.saveButton.textContent = "等待确认结果";
  }
  const action = kind === "delete" ? "删除" : "写入";
  updatePendingUI(
    "服务器可能已经完成，也可能没有收到请求。" +
      `为避免重复${action}，请先重新读取最新列表确认。` +
      `\n\n错误信息：${detail}`,
  );
}

function pendingReviewText(pending) {
  if (pending.kind === "create") {
    return (
      `已重新读取最新列表，当前共有 ${state.sites.length} 个站点。` +
      "系统不能仅凭名称或地址判断哪一条（如有）来自本次操作；" +
      "请关闭对话框查看完整列表并人工确认。确认没有本次记录后，" +
      "再重新打开新增表单创建。"
    );
  }
  const site = pending.targetId
    ? state.sites.find((item) => item.id === pending.targetId)
    : null;
  const current = site
    ? `仍在列表中：${site.name}（revision ${site.revision}，${
        site.enabled ? "已启用" : "已停用"
      }）。`
    : "不在列表中。";
  if (pending.kind === "edit") {
    return (
      `已重新读取最新列表。该站点当前${current}` +
      "请关闭对话框查看列表并判断是否符合预期。如需继续，" +
      "请关闭后重新打开编辑。"
    );
  }
  return (
    `已重新读取最新列表。该站点当前${current}` +
    "请关闭对话框确认是否符合预期；如需再次删除，请关闭后重新操作。"
  );
}

async function recheckPendingWrite() {
  const pending = state.pendingWrite;
  if (!pending || pending.phase === "checking") {
    return;
  }
  pending.phase = "checking";
  updatePendingUI("正在重新读取最新列表…");
  const succeeded = await loadSites();
  if (state.pendingWrite !== pending) {
    return;
  }
  if (succeeded) {
    pending.phase = "review";
    updatePendingUI(pendingReviewText(pending));
  } else {
    pending.phase = "unconfirmed";
    updatePendingUI(
      "仍无法确认结果：重新读取最新列表失败。请不要重复提交；" +
        "保持此页面，稍后再次重新读取。",
    );
  }
}

function clearPendingPanels() {
  elements.writeUncertain.hidden = true;
  elements.writeUncertainText.textContent = "";
  elements.reviewCloseButton.hidden = true;
  elements.deleteUncertain.hidden = true;
  elements.deleteUncertainText.textContent = "";
  elements.deleteReviewCloseButton.hidden = true;
}

function blockIfPendingWrite() {
  if (!state.pendingWrite) {
    return false;
  }
  showStatus(
    "有写操作结果尚未确认。请先重新读取最新列表并关闭确认面板。",
    "error",
  );
  return true;
}

function finishPendingReview() {
  const pending = state.pendingWrite;
  if (!pending || pending.phase !== "review") {
    return;
  }
  const isDelete = pending.kind === "delete";
  closeDialog(isDelete ? elements.deleteDialog : elements.siteDialog);
  state.pendingWrite = null;
  clearPendingPanels();
  if (isDelete) {
    state.deleteTarget = null;
  } else {
    setFormDisabled(false);
    elements.saveButton.textContent =
      state.mode === "create" ? "保存站点" : "保存更改";
  }
  showStatus(
    "已重新读取最新列表，并关闭未确认的操作。请根据列表判断结果；" +
      "如需继续，请重新打开对应表单。",
    "info",
  );
}

function openCreate() {
  if (blockIfPendingWrite()) {
    return;
  }
  clearPendingPanels();
  state.mode = "create";
  state.editingId = null;
  state.editingRevision = null;
  elements.siteForm.reset();
  elements.dialogTitle.textContent = "新增站点";
  elements.saveButton.textContent = "保存站点";
  elements.name.value = "";
  elements.baseUrl.value = "";
  elements.timeout.value = "10";
  elements.enabled.checked = false;
  clearFormError();
  resetCredentialForm();
  syncCredentialFields(false, "keep");
  setFormDisabled(false);
  openDialog(elements.siteDialog);
  elements.name.focus();
}

function openEdit(site) {
  if (blockIfPendingWrite()) {
    return;
  }
  clearPendingPanels();
  state.mode = "edit";
  state.editingId = site.id;
  state.editingRevision = site.revision;
  elements.siteForm.reset();
  elements.dialogTitle.textContent = "编辑站点";
  elements.saveButton.textContent = "保存更改";
  elements.name.value = site.name;
  elements.baseUrl.value = site.base_url;
  elements.timeout.value = String(site.timeout_seconds);
  elements.enabled.checked = site.enabled;
  clearFormError();
  resetCredentialForm();
  const keep = elements.siteForm.querySelector(
    'input[name="credential_action"][value="keep"]',
  );
  if (keep) {
    keep.checked = true;
  }
  syncCredentialFields(site.has_bearer_token, "keep");
  setFormDisabled(false);
  openDialog(elements.siteDialog);
  elements.name.focus();
  elements.name.select();
}

function formPayload() {
  return {
    name: elements.name.value,
    base_url: elements.baseUrl.value,
    enabled: elements.enabled.checked,
    timeout_seconds: Number(elements.timeout.value),
  };
}

async function handleSave(event) {
  event.preventDefault();
  if (state.saving || state.pendingWrite) {
    return;
  }
  clearFormError();
  if (!elements.siteForm.reportValidity()) {
    return;
  }

  state.saving = true;
  const idleLabel =
    state.mode === "create" ? "保存站点" : "保存更改";
  elements.saveButton.textContent = "保存中…";
  setFormDisabled(true);

  try {
    if (state.mode === "create") {
      const payload = formPayload();
      payload.bearer_token = elements.bearerToken.value;
      await bridge.apiPost("sites", payload);
    } else {
      const action = selectedCredentialAction();
      const payload = formPayload();
      payload.expected_revision = state.editingRevision;
      payload.credential_action = action;
      if (action === "set") {
        payload.bearer_token = elements.bearerToken.value;
      }
      await bridge.apiPost(
        `sites/${encodeURIComponent(state.editingId)}/update`,
        payload,
      );
    }

    closeDialog(elements.siteDialog);
    const reloaded = await loadSites();
    showStatus(
      reloaded
        ? "站点已保存。"
        : "站点已保存，但列表刷新失败，请点击“刷新”重试。",
      reloaded ? "success" : "error",
    );
  } catch (error) {
    const message = friendlyErrorMessage(error);
    if (state.mode === "edit" && isConflictMessage(message)) {
      closeDialog(elements.siteDialog);
      const reloaded = await loadSites();
      showStatus(
        reloaded
          ? "该站点已被其他操作修改，已重新加载最新数据。请重新打开编辑后再保存。"
          : "该站点已被其他操作修改，但列表刷新失败，请点击“刷新”重试。",
        "error",
      );
    } else if (state.mode === "edit" && isMissingMessage(message)) {
      closeDialog(elements.siteDialog);
      const reloaded = await loadSites();
      showStatus(
        reloaded
          ? "该站点已被删除，已重新加载最新数据。"
          : "该站点已被删除，但列表刷新失败，请点击“刷新”重试。",
        "error",
      );
    } else if (isExplicitWriteRejection(message)) {
      showFormError(message);
    } else {
      enterPendingWrite(
        state.mode,
        writeFailureDetail(error),
        state.mode === "edit" ? { id: state.editingId } : null,
      );
    }
  } finally {
    state.saving = false;
    if (!state.pendingWrite) {
      setFormDisabled(false);
      elements.saveButton.textContent = idleLabel;
    }
  }
}

function openDelete(site) {
  if (blockIfPendingWrite()) {
    return;
  }
  clearPendingPanels();
  state.deleteTarget = site;
  elements.deleteMessage.textContent = `确定删除“${site.name}”吗？删除后无法从此页面恢复。`;
  elements.deleteError.textContent = "";
  elements.deleteError.hidden = true;
  elements.deleteCancelButton.disabled = false;
  elements.deleteConfirmButton.disabled = false;
  elements.deleteConfirmButton.textContent = "删除";
  openDialog(elements.deleteDialog);
  elements.deleteCancelButton.focus();
}

async function confirmDelete() {
  if (state.deleting || state.pendingWrite || !state.deleteTarget) {
    return;
  }
  const target = state.deleteTarget;
  state.deleting = true;
  elements.deleteCancelButton.disabled = true;
  elements.deleteConfirmButton.disabled = true;
  elements.deleteConfirmButton.textContent = "删除中…";

  try {
    await bridge.apiPost(
      `sites/${encodeURIComponent(target.id)}/delete`,
      { expected_revision: target.revision },
    );
    closeDialog(elements.deleteDialog);
    state.deleteTarget = null;
    const reloaded = await loadSites();
    showStatus(
      reloaded
        ? `已删除“${target.name}”。`
        : `已删除“${target.name}”，但列表刷新失败，请点击“刷新”重试。`,
      reloaded ? "success" : "error",
    );
  } catch (error) {
    const message = friendlyErrorMessage(error);
    if (isConflictMessage(message) || isMissingMessage(message)) {
      closeDialog(elements.deleteDialog);
      state.deleteTarget = null;
      const reloaded = await loadSites();
      showStatus(
        reloaded
          ? "该站点已被修改或删除，已重新加载最新数据。请确认后重新操作。"
          : "该站点已被修改或删除，但列表刷新失败，请点击“刷新”重试。",
        "error",
      );
    } else if (isExplicitWriteRejection(message)) {
      elements.deleteError.textContent = message;
      elements.deleteError.hidden = false;
    } else {
      enterPendingWrite("delete", writeFailureDetail(error), target);
    }
  } finally {
    state.deleting = false;
    if (!state.pendingWrite) {
      elements.deleteCancelButton.disabled = false;
      elements.deleteConfirmButton.disabled = false;
      elements.deleteConfirmButton.textContent = "删除";
    }
  }
}

function bindEvents() {
  elements.refreshButton.addEventListener("click", loadSites);
  elements.retryButton.addEventListener("click", loadSites);
  elements.createButton.addEventListener("click", openCreate);
  elements.emptyCreateButton.addEventListener("click", openCreate);
  elements.previewButton.addEventListener("click", previewContent);
  elements.previewSite.addEventListener("change", () => {
    state.previewSiteId = elements.previewSite.value;
    resetPreviewResult();
    setPreviewBusy(false);
  });
  elements.previewContentType.addEventListener("change", () => {
    resetPreviewResult();
    setPreviewBusy(false);
  });
  elements.recheckWriteButton.addEventListener(
    "click",
    recheckPendingWrite,
  );
  elements.reviewCloseButton.addEventListener(
    "click",
    finishPendingReview,
  );
  elements.cancelButton.addEventListener("click", () => {
    if (!state.saving && !state.pendingWrite) {
      closeDialog(elements.siteDialog);
    }
  });
  elements.closeDialogButton.addEventListener("click", () => {
    if (!state.saving && !state.pendingWrite) {
      closeDialog(elements.siteDialog);
    }
  });
  elements.siteForm.addEventListener("submit", handleSave);
  elements.siteForm.addEventListener("change", (event) => {
    if (event.target.name !== "credential_action") {
      return;
    }
    const site = state.sites.find(
      (item) => item.id === state.editingId,
    );
    syncCredentialFields(
      site ? site.has_bearer_token : false,
      selectedCredentialAction(),
    );
  });
  elements.siteDialog.addEventListener("cancel", (event) => {
    if (state.saving || state.pendingWrite) {
      event.preventDefault();
    }
  });
  elements.deleteCancelButton.addEventListener("click", () => {
    if (!state.deleting && !state.pendingWrite) {
      closeDialog(elements.deleteDialog);
    }
  });
  elements.deleteConfirmButton.addEventListener("click", confirmDelete);
  elements.recheckDeleteButton.addEventListener(
    "click",
    recheckPendingWrite,
  );
  elements.deleteReviewCloseButton.addEventListener(
    "click",
    finishPendingReview,
  );
  elements.deleteDialog.addEventListener("cancel", (event) => {
    if (state.deleting || state.pendingWrite) {
      event.preventDefault();
    }
  });
  elements.deleteDialog.addEventListener("close", () => {
    if (!state.pendingWrite) {
      state.deleteTarget = null;
    }
  });
}

async function init() {
  if (!bridge) {
    showStatus(
      "无法加载插件页面通信组件，请刷新页面或重新打开插件视图。",
      "error",
    );
    elements.createButton.disabled = true;
    elements.refreshButton.disabled = true;
    elements.emptyState.hidden = true;
    elements.sitesList.hidden = true;
    setListState("插件页面通信组件不可用。", false);
    return;
  }

  try {
    const context = await bridge.ready();
    if (context && typeof context.pageTitle === "string") {
      document.title = `${context.pageTitle} · 60sAPI Hub`;
    }
  } catch {
    // The API can still work if the context message is delayed.
  }

  bindEvents();
  await loadSites();
}

init();
