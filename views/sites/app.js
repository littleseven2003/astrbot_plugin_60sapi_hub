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
  previewRefreshButton: document.getElementById("preview-refresh-button"),
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
  previewCacheState: document.getElementById("preview-cache-state"),
  previewCacheExpires: document.getElementById("preview-cache-expires"),
  previewWarnings: document.getElementById("preview-warnings"),
  previewEmpty: document.getElementById("preview-empty"),
  previewEmptyText: document.getElementById("preview-empty-text"),
  previewNewsList: document.getElementById("preview-news-list"),
  previewBiliList: document.getElementById("preview-bili-list"),
  previewZhihuList: document.getElementById("preview-zhihu-list"),
  previewDouyinList: document.getElementById("preview-douyin-list"),
  previewToutiaoList: document.getElementById("preview-toutiao-list"),
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
  profileCount: document.getElementById("profile-count"),
  profilesList: document.getElementById("profiles-list"),
  profileListState: document.getElementById("profile-list-state"),
  profileListStateText: document.getElementById("profile-list-state-text"),
  profileRetryButton: document.getElementById("profile-retry-button"),
  profileEmptyState: document.getElementById("profile-empty-state"),
  createProfileButton: document.getElementById("create-profile-button"),
  profileDialog: document.getElementById("profile-dialog"),
  profileForm: document.getElementById("profile-form"),
  profileDialogTitle: document.getElementById("profile-dialog-title"),
  profileName: document.getElementById("profile-name"),
  profileSite: document.getElementById("profile-site"),
  profileItemEditor: document.getElementById("profile-item-editor"),
  profileFormError: document.getElementById("profile-form-error"),
  profileWriteUncertain: document.getElementById("profile-write-uncertain"),
  profileWriteUncertainText: document.getElementById("profile-write-uncertain-text"),
  profileRecheckButton: document.getElementById("profile-recheck-button"),
  profileReviewCloseButton: document.getElementById("profile-review-close-button"),
  profileCloseDialogButton: document.getElementById("profile-close-dialog-button"),
  profileCancelButton: document.getElementById("profile-cancel-button"),
  profileSaveButton: document.getElementById("profile-save-button"),
  profileDeleteDialog: document.getElementById("profile-delete-dialog"),
  profileDeleteMessage: document.getElementById("profile-delete-message"),
  profileDeleteError: document.getElementById("profile-delete-error"),
  profileDeleteUncertain: document.getElementById("profile-delete-uncertain"),
  profileDeleteUncertainText: document.getElementById("profile-delete-uncertain-text"),
  profileDeleteRecheckButton: document.getElementById("profile-delete-recheck-button"),
  profileDeleteReviewCloseButton: document.getElementById("profile-delete-review-close-button"),
  profileDeleteCancelButton: document.getElementById("profile-delete-cancel-button"),
  profileDeleteConfirmButton: document.getElementById("profile-delete-confirm-button"),
  profilePreviewSelect: document.getElementById("profile-preview-select"),
  profilePreviewButton: document.getElementById("profile-preview-button"),
  profilePreviewRefreshButton: document.getElementById("profile-preview-refresh-button"),
  profilePreviewHint: document.getElementById("profile-preview-hint"),
  profilePreviewStatus: document.getElementById("profile-preview-status"),
  profilePreviewResult: document.getElementById("profile-preview-result"),
  profilePreviewName: document.getElementById("profile-preview-name"),
  profilePreviewSource: document.getElementById("profile-preview-source"),
  profilePreviewCounts: document.getElementById("profile-preview-counts"),
  profilePreviewState: document.getElementById("profile-preview-state"),
  profilePreviewSections: document.getElementById("profile-preview-sections"),
  profileCopyButton: document.getElementById("profile-copy-button"),
  profileDownloadButton: document.getElementById("profile-download-button"),
  profileOutputText: document.getElementById("profile-output-text"),
  profileOutputHint: document.getElementById("profile-output-hint"),
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
  profiles: [],
  profileLoading: false,
  profileSaving: false,
  profileDeleting: false,
  profileMode: "create",
  editingProfileId: null,
  editingProfileRevision: null,
  profileDeleteTarget: null,
  profilePending: null,
  profileEditorItems: [],
  profilePreviewLoading: false,
  profilePreviewId: null,
  profilePreviewRequestId: 0,
  profilePreviewBaseline: null,
  profilePreviewOutputs: null,
  profilePreviewFormat: "text",
  profileOutputEpoch: 0,
  profileCopyOperationId: 0,
};

const CONTENT_LABELS = {
  daily_news: "每日新闻",
  bilibili_hot: "Bilibili 热搜",
  zhihu_hot: "知乎热榜",
  douyin_hot: "抖音热搜",
  toutiao_hot: "今日头条热榜",
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
  /仍被内容组合引用/,
  /引用的站点不存在/,
  /内容组合不存在/,
  /内容组合存储暂不可用/,
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
  elements.previewCacheState.textContent = "";
  elements.previewCacheExpires.textContent = "";
  elements.previewWarnings.textContent = "";
  elements.previewWarnings.hidden = true;
  elements.previewEmpty.hidden = true;
  elements.previewEmptyText.textContent = "上游返回了合法的空内容。";
  elements.previewNewsList.replaceChildren();
  elements.previewNewsList.hidden = true;
  elements.previewBiliList.replaceChildren();
  elements.previewBiliList.hidden = true;
  elements.previewZhihuList.replaceChildren();
  elements.previewZhihuList.hidden = true;
  elements.previewDouyinList.replaceChildren();
  elements.previewDouyinList.hidden = true;
  elements.previewToutiaoList.replaceChildren();
  elements.previewToutiaoList.hidden = true;
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
    elements.previewRefreshButton.disabled = true;
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
  elements.previewRefreshButton.disabled = state.previewLoading;
  elements.previewHint.hidden = true;
}

function setPreviewBusy(busy) {
  state.previewLoading = busy;
  const hasSite = Boolean(elements.previewSite.value);
  elements.previewSite.disabled = busy || !hasSite;
  elements.previewContentType.disabled = busy || !hasSite;
  elements.previewButton.disabled = busy || !hasSite;
  elements.previewRefreshButton.disabled = busy || !hasSite;
  elements.previewButton.textContent = busy ? "正在获取…" : "获取内容";
  elements.previewRefreshButton.textContent = busy ? "正在刷新…" : "刷新上游";
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
  const isZhihu = contentType === "zhihu_hot";
  const isDouyin = contentType === "douyin_hot";
  const isToutiao = contentType === "toutiao_hot";
  const isDaily = contentType === "daily_news";
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
        : isZhihu
          ? "/v2/zhihu"
          : isDouyin
            ? "/v2/douyin"
            : isToutiao
              ? "/v2/toutiao"
              : "/v2/60s";
  elements.previewDateItem.hidden = !isDaily;
  elements.previewContentDate.textContent = isDaily
    ? typeof snapshot.content_date === "string" && snapshot.content_date
      ? snapshot.content_date
      : "未知"
    : "";
  elements.previewItemCount.textContent = `${snapshot.items.length} 条`;
  const cache =
    snapshot.cache && typeof snapshot.cache === "object"
      ? snapshot.cache
      : {};
  const cacheHit = cache.hit === true;
  elements.previewCacheState.textContent = cacheHit
    ? "缓存命中"
    : "本次上游获取";
  const cacheExpires = formatUpdatedAt(
    typeof cache.expires_at === "string" ? cache.expires_at : "",
  );
  elements.previewCacheExpires.textContent = cacheExpires || "未知";
  const fetchedAt =
    typeof snapshot.fetched_at === "string" ? snapshot.fetched_at : "";
  const displayFetchedAt = formatUpdatedAt(fetchedAt);
  elements.previewFetchedAt.textContent = displayFetchedAt || "未知";
  if (fetchedAt) {
    elements.previewFetchedAt.setAttribute("datetime", fetchedAt);
  }

  const warnings = [];
  if (isDaily) {
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

  const list = isToutiao
    ? elements.previewToutiaoList
    : isDouyin
      ? elements.previewDouyinList
      : isZhihu
        ? elements.previewZhihuList
        : isBili
          ? elements.previewBiliList
          : elements.previewNewsList;
  for (const candidate of [
    elements.previewNewsList,
    elements.previewBiliList,
    elements.previewZhihuList,
    elements.previewDouyinList,
    elements.previewToutiaoList,
  ]) {
    if (candidate !== list) {
      candidate.replaceChildren();
      candidate.hidden = true;
    }
  }
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
    if (isZhihu && typeof item.detail === "string" && item.detail.trim()) {
      const summary = document.createElement("p");
      summary.className = "news-summary";
      summary.textContent = item.detail;
      row.append(summary);
    }
    if (
      isZhihu &&
      typeof item.hot_value_desc === "string" &&
      item.hot_value_desc.trim()
    ) {
      const heat = document.createElement("p");
      heat.className = "news-heat";
      heat.textContent = item.hot_value_desc;
      row.append(heat);
    }
    if (
      (isDouyin || isToutiao) &&
      Number.isInteger(item.hot_value) &&
      item.hot_value >= 0
    ) {
      const heat = document.createElement("p");
      heat.className = "news-heat";
      heat.textContent = `热度：${item.hot_value}`;
      row.append(heat);
    }
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
  elements.previewEmptyText.textContent = isToutiao
    ? "上游返回了合法的空今日头条热榜列表（0 条）。"
    : isDouyin
      ? "上游返回了合法的空抖音热搜列表（0 条）。"
      : isZhihu
        ? "上游返回了合法的空知乎热榜列表（0 条）。"
        : isBili
          ? "上游返回了合法的空 Bilibili 热搜列表（0 条）。"
          : "上游返回了合法的空内容（0 条新闻）。";

  if (isDaily && typeof snapshot.tip === "string" && snapshot.tip.trim()) {
    elements.previewTip.textContent = `微语：${snapshot.tip}`;
    elements.previewTip.hidden = false;
  }
  if (
    isDaily &&
    typeof snapshot.source_link === "string" &&
    snapshot.source_link
  ) {
    elements.previewSourceLink.textContent =
      `来源链接：${snapshot.source_link}`;
    elements.previewSourceLink.hidden = false;
  }

  elements.previewResult.hidden = false;
  const sourceState = cacheHit ? "缓存命中" : "本次上游获取";
  showPreviewStatus(
    empty
      ? isToutiao
        ? `已获取，上游返回了合法的空今日头条热榜列表（${sourceState}）。`
        : isDouyin
          ? `已获取，上游返回了合法的空抖音热搜列表（${sourceState}）。`
          : isZhihu
            ? `已获取，上游返回了合法的空知乎热榜列表（${sourceState}）。`
            : isBili
              ? `已获取，上游返回了合法的空 Bilibili 热搜列表（${sourceState}）。`
              : `已获取，上游返回了合法的空内容（0 条新闻，${sourceState}）。`
      : `已获取：${label} ${snapshot.items.length} 条（${sourceState}）。`,
    "success",
  );
}

async function previewContent(forceRefresh = false) {
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
  showPreviewStatus(
    forceRefresh ? `正在刷新${label}（跳过缓存）…` : `正在获取${label}…`,
    "info",
  );
  setPreviewBusy(true);

  try {
    const snapshot = await bridge.apiPost(
      "content/preview",
      {
        site_id: siteId,
        content_type: contentType,
        force_refresh: Boolean(forceRefresh),
      },
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

function normalizeProfile(raw) {
  if (!raw || typeof raw !== "object" || typeof raw.id !== "string") {
    return null;
  }
  const items = Array.isArray(raw.items)
    ? raw.items
        .map((item) => {
          if (!item || typeof item !== "object") {
            return null;
          }
          const type = item.content_type;
          if (typeof type !== "string" || !CONTENT_LABELS[type]) {
            return null;
          }
          const limit = Number.isInteger(item.limit) && item.limit >= 1
            ? item.limit
            : null;
          return { content_type: type, limit };
        })
        .filter(Boolean)
    : [];
  return {
    id: raw.id,
    name: typeof raw.name === "string" ? raw.name : "未命名组合",
    site_id: typeof raw.site_id === "string" ? raw.site_id : "",
    site_name: typeof raw.site_name === "string" ? raw.site_name : "未知站点",
    site_enabled: raw.site_enabled === true,
    items,
    revision: Number.isInteger(raw.revision) ? raw.revision : 1,
    updated_at: typeof raw.updated_at === "string" ? raw.updated_at : "",
  };
}

function profileSummary(profile) {
  return profile.items
    .map((item) => {
      const label = CONTENT_LABELS[item.content_type] || item.content_type;
      return item.limit === null ? label : `${label}（${item.limit} 条）`;
    })
    .join(" · ");
}

function renderProfile(profile) {
  const row = document.createElement("li");
  row.className = "site-row profile-row";
  row.dataset.profileId = profile.id;

  const main = document.createElement("div");
  main.className = "site-main";
  const titleLine = document.createElement("div");
  titleLine.className = "site-title-line";
  const name = document.createElement("h3");
  name.className = "site-name";
  name.textContent = profile.name;
  const siteBadge = document.createElement("span");
  siteBadge.className = "badge";
  siteBadge.textContent = profile.site_name;
  const enabledBadge = document.createElement("span");
  enabledBadge.className = "badge";
  enabledBadge.dataset.enabled = String(profile.site_enabled);
  enabledBadge.textContent = profile.site_enabled ? "站点已启用" : "站点已停用";
  titleLine.append(name, siteBadge, enabledBadge);

  const summary = document.createElement("p");
  summary.className = "site-meta profile-summary";
  summary.textContent = profileSummary(profile) || "没有栏目";
  const meta = document.createElement("p");
  meta.className = "site-meta";
  addMeta(meta, "栏目", `${profile.items.length} 项`);
  addMeta(meta, "revision", String(profile.revision));
  const updated = formatUpdatedAt(profile.updated_at);
  if (updated) {
    addMeta(meta, "最近更新", updated);
  }
  main.append(titleLine, summary, meta);

  const actions = document.createElement("div");
  actions.className = "site-actions";
  actions.append(
    makeButton(
      "编辑",
      "button button-secondary",
      () => openEditProfile(profile),
      `编辑内容组合 ${profile.name}`,
    ),
    makeButton(
      "删除",
      "button button-danger",
      () => openProfileDelete(profile),
      `删除内容组合 ${profile.name}`,
    ),
  );
  row.append(main, actions);
  return row;
}

function renderProfiles() {
  elements.profileCount.textContent = `${state.profiles.length} 个`;
  elements.profilesList.replaceChildren();
  if (state.profiles.length === 0) {
    elements.profilesList.hidden = true;
    elements.profileEmptyState.hidden = false;
    clearProfileListState();
    return;
  }
  elements.profileEmptyState.hidden = true;
  elements.profilesList.hidden = false;
  clearProfileListState();
  for (const profile of state.profiles) {
    elements.profilesList.append(renderProfile(profile));
  }
}

function setProfileListState(message, showRetry) {
  elements.profileListStateText.textContent = message;
  elements.profileRetryButton.hidden = !showRetry;
  elements.profileListState.hidden = false;
}

function clearProfileListState() {
  elements.profileListStateText.textContent = "";
  elements.profileRetryButton.hidden = true;
  elements.profileListState.hidden = true;
}

function profileSignature(profile) {
  return JSON.stringify({
    id: profile.id,
    revision: profile.revision,
    name: profile.name,
    site_id: profile.site_id,
    site_name: profile.site_name,
    site_enabled: profile.site_enabled,
    items: profile.items.map((item) => [
      item.content_type,
      item.limit,
    ]),
  });
}

async function loadProfiles() {
  if (!bridge || state.profileLoading) {
    return false;
  }
  state.profileLoading = true;
  elements.profilesList.setAttribute("aria-busy", "true");
  if (state.profiles.length === 0) {
    elements.profileEmptyState.hidden = true;
    elements.profilesList.hidden = true;
    setProfileListState("正在读取内容组合…", false);
  }
  try {
    const result = await bridge.apiGet("profiles");
    const raw = result && Array.isArray(result.profiles) ? result.profiles : [];
    state.profiles = raw.map(normalizeProfile).filter(Boolean);
    const baseline = state.profilePreviewBaseline;
    if (baseline) {
      const current = state.profiles.find(
        (profile) => profile.id === state.profilePreviewId,
      );
      if (!current || profileSignature(current) !== baseline) {
        invalidateProfilePreview();
      }
    }
    renderProfiles();
    syncProfilePreview();
    return true;
  } catch (error) {
    showStatus(`读取内容组合失败：${friendlyErrorMessage(error)}`, "error");
    if (state.profiles.length === 0) {
      elements.profileEmptyState.hidden = true;
      elements.profilesList.hidden = true;
      setProfileListState("无法读取内容组合。请确认插件已启用，然后重试。", true);
    }
    return false;
  } finally {
    state.profileLoading = false;
    elements.profilesList.setAttribute("aria-busy", "false");
  }
}

function syncProfilePreview() {
  const profiles = state.profiles;
  if (!profiles.some((profile) => profile.id === state.profilePreviewId)) {
    state.profilePreviewId = profiles.length ? profiles[0].id : null;
  }
  elements.profilePreviewSelect.replaceChildren();
  if (profiles.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "没有已保存组合";
    elements.profilePreviewSelect.append(option);
    elements.profilePreviewSelect.disabled = true;
    elements.profilePreviewButton.disabled = true;
    elements.profilePreviewRefreshButton.disabled = true;
    elements.profilePreviewHint.textContent =
      "暂无内容组合。请先创建一个组合。";
    elements.profilePreviewHint.hidden = false;
    resetProfilePreview();
    return;
  }
  for (const profile of profiles) {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.name;
    elements.profilePreviewSelect.append(option);
  }
  elements.profilePreviewSelect.value = state.profilePreviewId;
  elements.profilePreviewSelect.disabled = state.profilePreviewLoading;
  elements.profilePreviewButton.disabled = state.profilePreviewLoading;
  elements.profilePreviewRefreshButton.disabled = state.profilePreviewLoading;
  elements.profilePreviewHint.hidden = true;
}

function setProfilePreviewBusy(busy) {
  state.profilePreviewLoading = busy;
  const hasProfile = Boolean(elements.profilePreviewSelect.value);
  elements.profilePreviewSelect.disabled = busy || !hasProfile;
  elements.profilePreviewButton.disabled = busy || !hasProfile;
  elements.profilePreviewRefreshButton.disabled = busy || !hasProfile;
  elements.profilePreviewButton.textContent = busy ? "正在获取…" : "获取组合";
  elements.profilePreviewRefreshButton.textContent = busy ? "正在刷新…" : "刷新组合";
  syncProfileOutputButtons();
}

function resetProfileOutput() {
  state.profileOutputEpoch += 1;
  state.profilePreviewOutputs = null;
  state.profilePreviewFormat = "text";
  elements.profileOutputText.value = "";
  elements.profileOutputHint.textContent = "";
  elements.profileOutputHint.hidden = true;
  const textRadio = elements.profilePreviewResult?.querySelector(
    'input[name="profile-output-format"][value="text"]',
  );
  if (textRadio) {
    textRadio.checked = true;
  }
  syncProfileOutputButtons();
}

function selectedProfileOutput() {
  if (!state.profilePreviewOutputs) {
    return "";
  }
  return state.profilePreviewFormat === "markdown"
    ? state.profilePreviewOutputs.markdown
    : state.profilePreviewOutputs.text;
}

function syncProfileOutputButtons() {
  const hasOutput = Boolean(selectedProfileOutput());
  elements.profileCopyButton.disabled =
    state.profilePreviewLoading || !hasOutput;
  elements.profileDownloadButton.disabled =
    state.profilePreviewLoading || !hasOutput;
  elements.profileDownloadButton.textContent =
    state.profilePreviewFormat === "markdown" ? "下载 .md" : "下载 .txt";
}

function renderProfileOutput() {
  state.profileOutputEpoch += 1;
  const output = selectedProfileOutput();
  elements.profileOutputText.value = output;
  elements.profileOutputHint.textContent = "";
  elements.profileOutputHint.hidden = true;
  syncProfileOutputButtons();
}

async function copyProfileOutput() {
  const output = selectedProfileOutput();
  if (!output || state.profilePreviewLoading) {
    return;
  }
  const operation = {
    id: ++state.profileCopyOperationId,
    epoch: state.profileOutputEpoch,
    format: state.profilePreviewFormat,
    text: output,
  };
  const ownsOperation = () =>
    operation.id === state.profileCopyOperationId &&
    operation.epoch === state.profileOutputEpoch &&
    operation.format === state.profilePreviewFormat &&
    operation.text === selectedProfileOutput();
  try {
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      throw new Error("clipboard unavailable");
    }
    await navigator.clipboard.writeText(output);
    if (!ownsOperation()) {
      return;
    }
    elements.profileOutputHint.textContent =
      `已复制${operation.format === "markdown" ? " Markdown 源码" : "纯文字"}。`;
    elements.profileOutputHint.hidden = false;
  } catch {
    if (!ownsOperation()) {
      return;
    }
    elements.profileOutputText.focus();
    elements.profileOutputText.select();
    elements.profileOutputHint.textContent =
      "无法访问剪贴板，已选中当前文本，请手动复制。";
    elements.profileOutputHint.hidden = false;
  }
}

function downloadProfileOutput() {
  const output = selectedProfileOutput();
  if (!output || state.profilePreviewLoading) {
    return;
  }
  state.profileOutputEpoch += 1;
  const extension = state.profilePreviewFormat === "markdown" ? "md" : "txt";
  const safeId = String(state.profilePreviewId || "profile")
    .replace(/[^A-Za-z0-9_-]/g, "")
    .slice(0, 64) || "profile";
  const filename = `60sapi-profile-${safeId}.${extension}`;
  try {
    const blob = new Blob([output], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.rel = "noopener";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    elements.profileOutputHint.textContent = `已下载 ${filename}。`;
    elements.profileOutputHint.hidden = false;
  } catch {
    elements.profileOutputHint.textContent = "无法生成下载文件。";
    elements.profileOutputHint.hidden = false;
  }
}

function clearProfilePreview() {
  elements.profilePreviewResult.hidden = true;
  resetProfileOutput();
  elements.profilePreviewName.textContent = "";
  elements.profilePreviewSource.textContent = "";
  elements.profilePreviewCounts.textContent = "";
  elements.profilePreviewState.textContent = "";
  elements.profilePreviewSections.replaceChildren();
  elements.profilePreviewStatus.hidden = true;
  elements.profilePreviewStatus.textContent = "";
  delete elements.profilePreviewStatus.dataset.tone;
}

function invalidateProfilePreview() {
  state.profilePreviewRequestId += 1;
  state.profilePreviewBaseline = null;
  state.profilePreviewLoading = false;
  clearProfilePreview();
  setProfilePreviewBusy(false);
}

function resetProfilePreview() {
  invalidateProfilePreview();
}

function invalidateIfPreviewing(profileId) {
  if (profileId && state.profilePreviewId === profileId) {
    invalidateProfilePreview();
  }
}

function profileSiteOptions(selectedId) {
  elements.profileSite.replaceChildren();
  if (state.sites.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "没有站点";
    elements.profileSite.append(option);
    elements.profileSite.disabled = true;
    return;
  }
  for (const site of state.sites) {
    const option = document.createElement("option");
    option.value = site.id;
    option.textContent = site.enabled ? site.name : `${site.name}（已停用）`;
    elements.profileSite.append(option);
  }
  const target = state.sites.some((site) => site.id === selectedId)
    ? selectedId
    : (state.sites.find((site) => site.enabled) || state.sites[0]).id;
  elements.profileSite.value = target;
  elements.profileSite.disabled = false;
}

function renderProfileEditor() {
  elements.profileItemEditor.replaceChildren();
  const selectedTypes = new Set(
    state.profileEditorItems.map((item) => item.content_type),
  );
  const orderedTypes = [
    ...state.profileEditorItems.map((item) => item.content_type),
    ...Object.keys(CONTENT_LABELS).filter(
      (type) => !selectedTypes.has(type),
    ),
  ];
  for (const type of orderedTypes) {
    const row = document.createElement("div");
    row.className = "profile-editor-row";
    row.dataset.contentType = type;
    const toggle = document.createElement("label");
    toggle.className = "profile-editor-toggle";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedTypes.has(type);
    checkbox.setAttribute("aria-label", `选择${CONTENT_LABELS[type]}`);
    const name = document.createElement("span");
    name.textContent = CONTENT_LABELS[type];
    toggle.append(checkbox, name);

    const limit = document.createElement("input");
    limit.type = "number";
    limit.className = "profile-limit-input";
    limit.min = "1";
    limit.max = "1000";
    limit.step = "1";
    limit.placeholder = "全部";
    limit.setAttribute("aria-label", `${CONTENT_LABELS[type]}最多展示条数`);
    limit.disabled = !checkbox.checked;
    const current = state.profileEditorItems.find(
      (item) => item.content_type === type,
    );
    limit.value = current ? current.limitText : "";

    const actions = document.createElement("div");
    actions.className = "profile-order-actions";
    const position = state.profileEditorItems.findIndex(
      (item) => item.content_type === type,
    );
    const up = makeButton("上移", "button button-quiet", () => {
      moveProfileItem(type, -1);
    });
    const down = makeButton("下移", "button button-quiet", () => {
      moveProfileItem(type, 1);
    });
    up.disabled = !checkbox.checked || position <= 0;
    down.disabled =
      !checkbox.checked || position < 0 ||
      position >= state.profileEditorItems.length - 1;
    actions.append(up, down);

    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.profileEditorItems.push({ content_type: type, limitText: "" });
      } else {
        state.profileEditorItems = state.profileEditorItems.filter(
          (item) => item.content_type !== type,
        );
      }
      renderProfileEditor();
    });
    limit.addEventListener("input", () => {
      const entry = state.profileEditorItems.find(
        (item) => item.content_type === type,
      );
      if (entry) {
        entry.limitText = limit.value;
      }
    });
    row.append(toggle, limit, actions);
    elements.profileItemEditor.append(row);
  }
}

function moveProfileItem(type, delta) {
  const index = state.profileEditorItems.findIndex(
    (item) => item.content_type === type,
  );
  const target = index + delta;
  if (index < 0 || target < 0 || target >= state.profileEditorItems.length) {
    return;
  }
  const [entry] = state.profileEditorItems.splice(index, 1);
  state.profileEditorItems.splice(target, 0, entry);
  renderProfileEditor();
}

function collectProfileItems() {
  if (state.profileEditorItems.length === 0) {
    throw new Error("请至少选择一个栏目。");
  }
  return state.profileEditorItems.map((entry) => {
    const raw = entry.limitText.trim();
    let limit = null;
    if (raw !== "") {
      if (!/^\d+$/.test(raw)) {
        throw new Error(
          `${CONTENT_LABELS[entry.content_type]}的条数必须是正整数或留空。`,
        );
      }
      limit = Number(raw);
      if (limit < 1 || limit > 1000) {
        throw new Error(
          `${CONTENT_LABELS[entry.content_type]}的条数必须在 1～1000 之间。`,
        );
      }
    }
    return {
      content_type: entry.content_type,
      params: {},
      limit,
    };
  });
}

function showProfileFormError(message) {
  elements.profileFormError.textContent = message;
  elements.profileFormError.hidden = false;
}

function clearProfileFormError() {
  elements.profileFormError.textContent = "";
  elements.profileFormError.hidden = true;
}

function setProfileFormDisabled(disabled) {
  for (const control of elements.profileForm.querySelectorAll(
    "input, select, button",
  )) {
    control.disabled = disabled;
  }
  elements.profileForm.setAttribute("aria-busy", String(disabled));
}

function openCreateProfile() {
  if (blockIfPendingWrite()) {
    return;
  }
  clearProfilePendingPanels();
  state.profileMode = "create";
  state.editingProfileId = null;
  state.editingProfileRevision = null;
  state.profileEditorItems = [{ content_type: "daily_news", limitText: "" }];
  elements.profileForm.reset();
  elements.profileDialogTitle.textContent = "新增组合";
  elements.profileName.value = "";
  profileSiteOptions(null);
  clearProfileFormError();
  setProfileFormDisabled(false);
  if (state.sites.length === 0) {
    showProfileFormError("请先创建至少一个站点。");
    elements.profileSaveButton.disabled = true;
  } else {
    elements.profileSaveButton.textContent = "保存组合";
  }
  renderProfileEditor();
  openDialog(elements.profileDialog);
  elements.profileName.focus();
}

function openEditProfile(profile) {
  if (blockIfPendingWrite()) {
    return;
  }
  clearProfilePendingPanels();
  state.profileMode = "edit";
  state.editingProfileId = profile.id;
  state.editingProfileRevision = profile.revision;
  state.profileEditorItems = profile.items.map((item) => ({
    content_type: item.content_type,
    limitText: item.limit === null ? "" : String(item.limit),
  }));
  elements.profileDialogTitle.textContent = "编辑组合";
  elements.profileName.value = profile.name;
  profileSiteOptions(profile.site_id);
  clearProfileFormError();
  setProfileFormDisabled(false);
  elements.profileSaveButton.textContent = "保存更改";
  renderProfileEditor();
  openDialog(elements.profileDialog);
  elements.profileName.focus();
  elements.profileName.select();
}

async function handleProfileSave(event) {
  event.preventDefault();
  if (state.profileSaving || state.profilePending || state.pendingWrite) {
    return;
  }
  clearProfileFormError();
  if (!elements.profileForm.reportValidity()) {
    return;
  }
  let items;
  try {
    items = collectProfileItems();
  } catch (error) {
    showProfileFormError(friendlyErrorMessage(error));
    return;
  }
  if (!elements.profileSite.value) {
    showProfileFormError("请选择站点。");
    return;
  }
  state.profileSaving = true;
  const idleLabel = state.profileMode === "create" ? "保存组合" : "保存更改";
  elements.profileSaveButton.textContent = "保存中…";
  setProfileFormDisabled(true);
  try {
    const payload = {
      name: elements.profileName.value,
      site_id: elements.profileSite.value,
      items,
    };
    if (state.profileMode === "create") {
      await bridge.apiPost("profiles", payload);
    } else {
      payload.expected_revision = state.editingProfileRevision;
      await bridge.apiPost(
        `profiles/${encodeURIComponent(state.editingProfileId)}/update`,
        payload,
      );
      invalidateIfPreviewing(state.editingProfileId);
    }
    closeDialog(elements.profileDialog);
    const reloaded = await loadProfiles();
    showStatus(
      reloaded ? "内容组合已保存。" : "内容组合已保存，但列表刷新失败，请重试。",
      reloaded ? "success" : "error",
    );
  } catch (error) {
    const message = friendlyErrorMessage(error);
    if (state.profileMode === "edit" && isConflictMessage(message)) {
      invalidateIfPreviewing(state.editingProfileId);
      closeDialog(elements.profileDialog);
      const reloaded = await loadProfiles();
      showStatus(
        reloaded
          ? "该组合已被其他操作修改，已重新加载最新数据。请重新编辑。"
          : "该组合已被其他操作修改，但列表刷新失败。",
        "error",
      );
    } else if (message.includes("引用的站点不存在")) {
      showProfileFormError(message);
    } else if (isMissingMessage(message)) {
      invalidateIfPreviewing(state.editingProfileId);
      closeDialog(elements.profileDialog);
      const reloaded = await loadProfiles();
      showStatus(
        reloaded
          ? "该组合已被删除，已重新加载最新数据。"
          : "该组合已被删除，但列表刷新失败。",
        "error",
      );
    } else if (isExplicitWriteRejection(message)) {
      showProfileFormError(message);
    } else {
      enterProfilePending(
        state.profileMode,
        writeFailureDetail(error),
      );
    }
  } finally {
    state.profileSaving = false;
    if (!state.profilePending) {
      setProfileFormDisabled(false);
      elements.profileSaveButton.textContent = idleLabel;
    }
  }
}

function openProfileDelete(profile) {
  if (blockIfPendingWrite()) {
    return;
  }
  clearProfilePendingPanels();
  state.profileDeleteTarget = profile;
  elements.profileDeleteMessage.textContent =
    `确定删除“${profile.name}”吗？删除组合不会删除站点或内容缓存。`;
  elements.profileDeleteError.textContent = "";
  elements.profileDeleteError.hidden = true;
  elements.profileDeleteCancelButton.disabled = false;
  elements.profileDeleteConfirmButton.disabled = false;
  elements.profileDeleteConfirmButton.textContent = "删除";
  openDialog(elements.profileDeleteDialog);
  elements.profileDeleteCancelButton.focus();
}

async function confirmProfileDelete() {
  if (state.profileDeleting || state.profilePending || state.pendingWrite ||
      !state.profileDeleteTarget) {
    return;
  }
  const target = state.profileDeleteTarget;
  state.profileDeleting = true;
  elements.profileDeleteCancelButton.disabled = true;
  elements.profileDeleteConfirmButton.disabled = true;
  elements.profileDeleteConfirmButton.textContent = "删除中…";
  try {
    await bridge.apiPost(
      `profiles/${encodeURIComponent(target.id)}/delete`,
      { expected_revision: target.revision },
    );
    invalidateIfPreviewing(target.id);
    closeDialog(elements.profileDeleteDialog);
    state.profileDeleteTarget = null;
    const reloaded = await loadProfiles();
    showStatus(
      reloaded ? `已删除“${target.name}”。` : "组合已删除，但列表刷新失败。",
      reloaded ? "success" : "error",
    );
  } catch (error) {
    const message = friendlyErrorMessage(error);
    if (isConflictMessage(message) || isMissingMessage(message)) {
      invalidateIfPreviewing(target.id);
      closeDialog(elements.profileDeleteDialog);
      state.profileDeleteTarget = null;
      const reloaded = await loadProfiles();
      showStatus(
        reloaded
          ? "该组合已被修改或删除，已重新加载最新数据。"
          : "该组合已被修改或删除，但列表刷新失败。",
        "error",
      );
    } else if (isExplicitWriteRejection(message)) {
      elements.profileDeleteError.textContent = message;
      elements.profileDeleteError.hidden = false;
    } else {
      enterProfilePending("profile_delete", writeFailureDetail(error), target);
    }
  } finally {
    state.profileDeleting = false;
    if (!state.profilePending) {
      elements.profileDeleteCancelButton.disabled = false;
      elements.profileDeleteConfirmButton.disabled = false;
      elements.profileDeleteConfirmButton.textContent = "删除";
    }
  }
}

function profilePendingElements(pending) {
  if (pending.kind === "profile_delete") {
    return {
      panel: elements.profileDeleteUncertain,
      text: elements.profileDeleteUncertainText,
      recheck: elements.profileDeleteRecheckButton,
      review: elements.profileDeleteReviewCloseButton,
    };
  }
  return {
    panel: elements.profileWriteUncertain,
    text: elements.profileWriteUncertainText,
    recheck: elements.profileRecheckButton,
    review: elements.profileReviewCloseButton,
  };
}

function updateProfilePendingUI(text) {
  const pending = state.profilePending;
  if (!pending) {
    return;
  }
  const ui = profilePendingElements(pending);
  ui.panel.hidden = false;
  ui.text.textContent = text;
  if (pending.kind === "profile_delete") {
    elements.profileDeleteCancelButton.disabled = true;
    elements.profileDeleteConfirmButton.disabled = true;
  } else {
    setProfileFormDisabled(true);
    elements.profileSaveButton.disabled = true;
    elements.profileSaveButton.textContent = "等待确认结果";
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
    ui.recheck.textContent = "重新读取最新组合";
    ui.review.hidden = true;
  }
}

function enterProfilePending(kind, detail, target) {
  state.profilePending = {
    kind,
    phase: "unconfirmed",
    targetId: target ? target.id : null,
  };
  if (kind === "profile_delete") {
    elements.profileDeleteError.hidden = true;
    elements.profileDeleteError.textContent = "";
  } else {
    clearProfileFormError();
  }
  updateProfilePendingUI(
    "服务器可能已经完成，也可能没有收到请求。" +
      "为避免重复写入，请先重新读取最新组合列表确认。" +
      `\n\n错误信息：${detail}`,
  );
}

function profilePendingReviewText(pending) {
  if (pending.kind === "create") {
    return (
      `已重新读取最新组合列表，当前共有 ${state.profiles.length} 个组合。` +
      "系统不能仅凭名称判断哪一条（如有）来自本次操作；" +
      "请关闭对话框查看列表并人工确认。"
    );
  }
  const profile = pending.targetId
    ? state.profiles.find((item) => item.id === pending.targetId)
    : null;
  const current = profile
    ? `仍在列表中：${profile.name}（revision ${profile.revision}）。`
    : "不在列表中。";
  return (
    `已重新读取最新组合列表。该组合当前${current}` +
    "请关闭对话框查看列表并判断是否符合预期；如需继续，请重新打开对应操作。"
  );
}

async function recheckProfilePending() {
  const pending = state.profilePending;
  if (!pending || pending.phase === "checking") {
    return;
  }
  pending.phase = "checking";
  updateProfilePendingUI("正在重新读取最新组合…");
  const succeeded = await loadProfiles();
  if (state.profilePending !== pending) {
    return;
  }
  if (succeeded) {
    pending.phase = "review";
    updateProfilePendingUI(profilePendingReviewText(pending));
  } else {
    pending.phase = "unconfirmed";
    updateProfilePendingUI(
      "仍无法确认结果：重新读取最新组合失败。请不要重复提交；" +
        "保持此页面，稍后再次读取。",
    );
  }
}

function clearProfilePendingPanels() {
  elements.profileWriteUncertain.hidden = true;
  elements.profileWriteUncertainText.textContent = "";
  elements.profileReviewCloseButton.hidden = true;
  elements.profileDeleteUncertain.hidden = true;
  elements.profileDeleteUncertainText.textContent = "";
  elements.profileDeleteReviewCloseButton.hidden = true;
}

function finishProfilePendingReview() {
  const pending = state.profilePending;
  if (!pending || pending.phase !== "review") {
    return;
  }
  const deleting = pending.kind === "profile_delete";
  closeDialog(deleting ? elements.profileDeleteDialog : elements.profileDialog);
  state.profilePending = null;
  clearProfilePendingPanels();
  if (deleting) {
    state.profileDeleteTarget = null;
  } else {
    setProfileFormDisabled(false);
    elements.profileSaveButton.textContent =
      state.profileMode === "create" ? "保存组合" : "保存更改";
  }
  showStatus(
    "已重新读取最新组合列表，并关闭未确认的操作。请根据列表判断结果。",
    "info",
  );
}

function profileSectionCacheText(section) {
  const cache = section.cache && typeof section.cache === "object"
    ? section.cache
    : {};
  return cache.hit === true ? "缓存命中" : "本次上游获取";
}

function appendProfileSectionItems(container, section) {
  const items = Array.isArray(section.items) ? section.items : [];
  if (items.length === 0) {
    const empty = document.createElement("p");
    empty.className = "preview-empty";
    empty.textContent = "上游返回了合法的空列表（0 条）。";
    container.append(empty);
    return;
  }
  const list = document.createElement("ul");
  list.className = "news-list";
  for (const item of items) {
    if (!item || typeof item !== "object" || typeof item.title !== "string" ||
        !item.title.trim()) {
      continue;
    }
    const row = document.createElement("li");
    const title = document.createElement("p");
    title.className = "news-title";
    title.textContent = item.title;
    row.append(title);
    if (typeof item.detail === "string" && item.detail.trim()) {
      const detail = document.createElement("p");
      detail.className = "news-summary";
      detail.textContent = item.detail;
      row.append(detail);
    }
    if (typeof item.hot_value_desc === "string" && item.hot_value_desc.trim()) {
      const heat = document.createElement("p");
      heat.className = "news-heat";
      heat.textContent = item.hot_value_desc;
      row.append(heat);
    }
    if (Number.isInteger(item.hot_value) && item.hot_value >= 0) {
      const heat = document.createElement("p");
      heat.className = "news-heat";
      heat.textContent = `热度：${item.hot_value}`;
      row.append(heat);
    }
    if (typeof item.link === "string" && item.link) {
      const link = document.createElement("p");
      link.className = "news-link";
      link.textContent = `链接：${item.link}`;
      row.append(link);
    }
    list.append(row);
  }
  container.append(list);
}

function renderProfileSection(section) {
  const article = document.createElement("article");
  article.className = "profile-section";
  article.dataset.status = section.status === "error" ? "error" : "ok";
  const head = document.createElement("div");
  head.className = "profile-section-head";
  const title = document.createElement("h3");
  title.textContent = section.label || CONTENT_LABELS[section.content_type] ||
    section.content_type || "栏目";
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = section.status === "error" ? "获取失败" : "成功";
  head.append(title, badge);
  article.append(head);

  if (section.status === "error") {
    const error = document.createElement("p");
    error.className = "profile-section-error";
    error.textContent = `${section.message || "栏目获取失败。"}（${section.code || "error"}）`;
    article.append(error);
    return article;
  }

  const meta = document.createElement("p");
  meta.className = "site-meta";
  const endpoint = typeof section.endpoint === "string" ? section.endpoint : "—";
  addMeta(meta, "接口", endpoint);
  const total = Number.isInteger(section.total_item_count)
    ? section.total_item_count
    : (Array.isArray(section.items) ? section.items.length : 0);
  const shown = Number.isInteger(section.displayed_count)
    ? section.displayed_count
    : (Array.isArray(section.items) ? section.items.length : 0);
  addMeta(meta, "条数", section.limit === null
    ? `${shown} / ${total}（全部）`
    : `${shown} / ${total}（最多 ${section.limit}）`);
  addMeta(meta, "缓存", profileSectionCacheText(section));
  if (typeof section.content_date === "string" && section.content_date) {
    addMeta(meta, "内容日期", section.content_date);
  }
  const fetched = formatUpdatedAt(
    typeof section.fetched_at === "string" ? section.fetched_at : "",
  );
  if (fetched) {
    addMeta(meta, "获取时间", fetched);
  }
  article.append(meta);
  if (Array.isArray(section.warnings) && section.warnings.length > 0) {
    const warning = document.createElement("p");
    warning.className = "preview-warning";
    warning.textContent = section.warnings.join("\n");
    article.append(warning);
  }
  appendProfileSectionItems(article, section);
  return article;
}

function renderProfilePreview(data) {
  if (!data || !Array.isArray(data.sections)) {
    throw new Error("组合预览数据格式异常。");
  }
  elements.profilePreviewName.textContent = data.profile_name || "未知组合";
  elements.profilePreviewSource.textContent = data.site_name || "未知站点";
  elements.profilePreviewCounts.textContent =
    `${data.success_count || 0} 成功 / ${data.failure_count || 0} 失败`;
  const stateText = data.status === "complete"
    ? "完整成功"
    : data.status === "partial"
      ? "部分成功"
      : "全部失败";
  elements.profilePreviewState.textContent = stateText;
  elements.profilePreviewSections.replaceChildren();
  for (const section of data.sections) {
    elements.profilePreviewSections.append(renderProfileSection(section));
  }
  if (
    !data.outputs ||
    typeof data.outputs.text !== "string" ||
    typeof data.outputs.markdown !== "string"
  ) {
    throw new Error("组合输出数据格式异常。");
  }
  state.profilePreviewOutputs = {
    text: data.outputs.text,
    markdown: data.outputs.markdown,
  };
  state.profilePreviewFormat = "text";
  const textRadio = elements.profilePreviewResult.querySelector(
    'input[name="profile-output-format"][value="text"]',
  );
  if (textRadio) {
    textRadio.checked = true;
  }
  renderProfileOutput();
  elements.profilePreviewResult.hidden = false;
  const tone = data.status === "complete"
    ? "success"
    : data.status === "partial"
      ? "info"
      : "error";
  const message = data.status === "complete"
    ? `组合预览完成：${data.success_count} 个栏目成功。`
    : data.status === "partial"
      ? `组合预览部分成功：${data.success_count} 个成功，${data.failure_count} 个失败。`
      : "组合预览全部失败。";
  showProfilePreviewStatus(message, tone);
}

function showProfilePreviewStatus(message, tone = "info") {
  elements.profilePreviewStatus.textContent = message;
  elements.profilePreviewStatus.dataset.tone = tone;
  elements.profilePreviewStatus.hidden = false;
}

async function previewProfile(forceRefresh = false) {
  if (!bridge || state.profilePreviewLoading) {
    return;
  }
  const profileId = elements.profilePreviewSelect.value;
  if (!profileId) {
    showProfilePreviewStatus("请先选择一个已保存组合。", "error");
    return;
  }
  const profile = state.profiles.find((item) => item.id === profileId);
  if (!profile) {
    showProfilePreviewStatus("组合不存在或已被删除，请重新选择。", "error");
    return;
  }
  const requestSignature = profileSignature(profile);
  const requestRevision = profile.revision;
  const requestId = ++state.profilePreviewRequestId;
  state.profilePreviewBaseline = requestSignature;
  state.profilePreviewLoading = true;
  clearProfilePreview();
  showProfilePreviewStatus(
    forceRefresh ? "正在刷新组合（跳过缓存）…" : "正在获取组合…",
    "info",
  );
  setProfilePreviewBusy(true);
  try {
    const data = await bridge.apiPost(
      `profiles/${encodeURIComponent(profileId)}/preview`,
      { force_refresh: Boolean(forceRefresh) },
    );
    const currentProfile = state.profiles.find(
      (item) => item.id === profileId,
    );
    const currentSignature = currentProfile
      ? profileSignature(currentProfile)
      : null;
    const revisionMatches =
      currentProfile &&
      currentProfile.revision === requestRevision &&
      data &&
      data.profile_id === profileId &&
      data.profile_revision === requestRevision;
    if (
      requestId !== state.profilePreviewRequestId ||
      elements.profilePreviewSelect.value !== profileId ||
      !currentProfile ||
      currentSignature !== requestSignature ||
      !revisionMatches
    ) {
      if (requestId === state.profilePreviewRequestId) {
        invalidateProfilePreview();
      }
      return;
    }
    renderProfilePreview(data);
  } catch (error) {
    if (requestId === state.profilePreviewRequestId) {
      state.profilePreviewBaseline = null;
      invalidateProfilePreview();
      showProfilePreviewStatus(
        `组合获取失败：${friendlyErrorMessage(error)}`,
        "error",
      );
    }
  } finally {
    if (requestId === state.profilePreviewRequestId) {
      state.profilePreviewLoading = false;
      setProfilePreviewBusy(false);
    }
  }
}


function blockIfPendingWrite() {
  if (state.pendingWrite) {
    showStatus(
      "有站点写操作结果尚未确认。请先重新读取最新列表并关闭确认面板。",
      "error",
    );
    return true;
  }
  if (state.profilePending) {
    showStatus(
      "有内容组合写操作结果尚未确认。请先重新读取最新组合并关闭确认面板。",
      "error",
    );
    return true;
  }
  return false;
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

async function refreshAll() {
  await Promise.all([loadSites(), loadProfiles()]);
}

function bindEvents() {
  elements.createProfileButton.addEventListener("click", openCreateProfile);
  elements.profileRetryButton.addEventListener("click", loadProfiles);
  elements.profilePreviewButton.addEventListener(
    "click",
    () => previewProfile(false),
  );
  elements.profilePreviewRefreshButton.addEventListener(
    "click",
    () => previewProfile(true),
  );
  elements.profilePreviewResult.addEventListener("change", (event) => {
    if (
      event.target instanceof HTMLInputElement &&
      event.target.name === "profile-output-format"
    ) {
      state.profilePreviewFormat =
        event.target.value === "markdown" ? "markdown" : "text";
      renderProfileOutput();
    }
  });
  elements.profileCopyButton.addEventListener("click", copyProfileOutput);
  elements.profileDownloadButton.addEventListener(
    "click",
    downloadProfileOutput,
  );
  elements.profilePreviewSelect.addEventListener("change", () => {
    state.profilePreviewId = elements.profilePreviewSelect.value;
    resetProfilePreview();
    setProfilePreviewBusy(false);
  });
  elements.profileForm.addEventListener("submit", handleProfileSave);
  elements.profileRecheckButton.addEventListener(
    "click",
    recheckProfilePending,
  );
  elements.profileReviewCloseButton.addEventListener(
    "click",
    finishProfilePendingReview,
  );
  elements.profileDeleteRecheckButton.addEventListener(
    "click",
    recheckProfilePending,
  );
  elements.profileDeleteReviewCloseButton.addEventListener(
    "click",
    finishProfilePendingReview,
  );
  elements.profileCloseDialogButton.addEventListener("click", () => {
    if (!state.profileSaving && !state.profilePending) {
      closeDialog(elements.profileDialog);
    }
  });
  elements.profileCancelButton.addEventListener("click", () => {
    if (!state.profileSaving && !state.profilePending) {
      closeDialog(elements.profileDialog);
    }
  });
  elements.profileDialog.addEventListener("cancel", (event) => {
    if (state.profileSaving || state.profilePending) {
      event.preventDefault();
    }
  });
  elements.profileDeleteCancelButton.addEventListener("click", () => {
    if (!state.profileDeleting && !state.profilePending) {
      closeDialog(elements.profileDeleteDialog);
    }
  });
  elements.profileDeleteConfirmButton.addEventListener(
    "click",
    confirmProfileDelete,
  );
  elements.profileDeleteDialog.addEventListener("cancel", (event) => {
    if (state.profileDeleting || state.profilePending) {
      event.preventDefault();
    }
  });
  elements.profileDeleteDialog.addEventListener("close", () => {
    if (!state.profilePending) {
      state.profileDeleteTarget = null;
    }
  });
  elements.refreshButton.addEventListener("click", refreshAll);
  elements.retryButton.addEventListener("click", refreshAll);
  elements.createButton.addEventListener("click", openCreate);
  elements.emptyCreateButton.addEventListener("click", openCreate);
  elements.previewButton.addEventListener("click", () => previewContent(false));
  elements.previewRefreshButton.addEventListener(
    "click",
    () => previewContent(true),
  );
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
  await Promise.all([loadSites(), loadProfiles()]);
}

init();
