const bridge = window.AstrBotPluginView;

// 站点来源类型：只接受明确取值。未知时用空字符串表示，界面必须显式提示，
// 既不能当成“公开服务”展示，也不能在保存时写回推断值。
const SOURCE_KIND_PUBLIC = "public";
const SOURCE_KIND_SELF_BUILT = "self_built";
const SOURCE_KIND_UNKNOWN = "";

function isKnownSourceKind(value) {
  return value === SOURCE_KIND_PUBLIC || value === SOURCE_KIND_SELF_BUILT;
}


// 60s API 官方文档的公共实例列表（核对日期见 docs）：已剔除文档中带删除线的
// 失效条目，保留完整基础地址与路径前缀；预设只在用户主动选择时填入，
// 页面不会因为打开表单或切换预设而请求这些站点。
const PRESET_INSTANCES = [
  { value: "https://60s.viki.moe", label: "60s.viki.moe（官方主域名）" },
  { value: "https://60s.crystelf.top", label: "60s.crystelf.top" },
  { value: "https://api.elysiayanyu.top", label: "api.elysiayanyu.top" },
  { value: "https://60s.7se.cn", label: "60s.7se.cn" },
  { value: "https://60s.mizhoubaobei.top", label: "60s.mizhoubaobei.top" },
  { value: "https://api.cczo.cc/60s", label: "api.cczo.cc/60s" },
  { value: "https://60s.zellon.top", label: "60s.zellon.top" },
  { value: "https://60s.superjeason.qzz.io", label: "60s.superjeason.qzz.io" },
  { value: "https://60s.okbaike.com", label: "60s.okbaike.com" },
];

function originOf(raw) {
  try {
    const url = new URL(String(raw || ""));
    return url.protocol + "//" + url.host;
  } catch {
    return "";
  }
}

function presetUrlOf(value) {
  return PRESET_INSTANCES.some((item) => item.value === value) ? value : "custom";
}

function rememberCustomAddress() {
  const current = elements.baseUrl.value;
  if (!PRESET_INSTANCES.some((item) => item.value === current)) {
    state.customBaseUrl = current;
  }
}

function syncInstanceTypeUI() {
  const kind = elements.sourceKind.value;
  const isPublic = kind === SOURCE_KIND_PUBLIC;
  const isSelf = kind === SOURCE_KIND_SELF_BUILT;
  elements.instanceTypePublic.checked = isPublic;
  elements.instanceTypeSelf.checked = isSelf;
  elements.presetField.hidden = !isPublic;
  const usePreset = isPublic && elements.instancePreset.value !== "custom";
  elements.baseUrlField.hidden = usePreset;
  elements.instanceTypeHint.textContent = isSelf
    ? "自建实例：内容输出只显示“自建服务”，不会暴露地址。"
    : isPublic
      ? "公开实例：内容输出只显示域名。"
      : "当前站点的来源类型无法识别；选择实例类型后会按所选类型保存。";
}

/** 任何会改动实际服务地址的操作都要走同一凭据判断。 */
function applyAddressChange() {
  syncInstanceTypeUI();
  handleBaseUrlChanged();
}

function selectInstanceType(kind) {
  const wasPreset = PRESET_INSTANCES.some(
    (item) => item.value === elements.baseUrl.value,
  );
  elements.sourceKind.value = kind;
  if (kind === SOURCE_KIND_SELF_BUILT) {
    elements.instancePreset.value = "custom";
    // 自建实例不该沿用公共预设地址：回到用户自己填写的地址。
    if (wasPreset) elements.baseUrl.value = state.customBaseUrl || "";
  } else if (kind === SOURCE_KIND_PUBLIC && state.customBaseUrl) {
    // 公开实例默认回到自定义，保留用户当前输入。
    elements.instancePreset.value = "custom";
  }
  setSourceKindHint(false);
  applyAddressChange();
}
function sourceKindLabel(value) {
  if (value === SOURCE_KIND_PUBLIC) {
    return "公开服务";
  }
  if (value === SOURCE_KIND_SELF_BUILT) {
    return "自建服务";
  }
  return "来源类型未知";
}

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
  previewSourceUrl: document.getElementById("preview-source-url"),
  sourceKind: document.getElementById("source-kind"),
  sourceKindHint: document.getElementById("source-kind-hint"),
  instanceTypePublic: document.getElementById("instance-type-public"),
  instanceTypeSelf: document.getElementById("instance-type-self"),
  instanceTypeHint: document.getElementById("instance-type-hint"),
  presetField: document.getElementById("preset-field"),
  instancePreset: document.getElementById("instance-preset"),
  baseUrlField: document.getElementById("base-url-field"),
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
  profilePreviewSite: document.getElementById("profile-preview-site"),
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
  profileImageButton: document.getElementById("profile-image-button"),
  profileImageState: document.getElementById("profile-image-state"),
  profileImageHint: document.getElementById("profile-image-hint"),
  profileImagePages: document.getElementById("profile-image-pages"),
  profileImageViewer: document.getElementById("profile-image-viewer"),
  profileImageViewerTitle: document.getElementById("profile-image-viewer-title"),
  profileImageViewerBody: document.getElementById("profile-image-viewer-body"),
  profileImageViewerImg: document.getElementById("profile-image-viewer-img"),
  profileImageViewerHint: document.getElementById("profile-image-viewer-hint"),
  profileImageViewerClose: document.getElementById("profile-image-viewer-close"),
  profileImageViewerDownload: document.getElementById("profile-image-viewer-download"),
  profileImageViewerZoom: document.getElementById("profile-image-viewer-zoom"),
  taskCount: document.getElementById("task-count"),
  tasksList: document.getElementById("tasks-list"),
  taskListState: document.getElementById("task-list-state"),
  taskListStateText: document.getElementById("task-list-state-text"),
  taskRetryButton: document.getElementById("task-retry-button"),
  taskEmptyState: document.getElementById("task-empty-state"),
  createTaskButton: document.getElementById("create-task-button"),
  taskDialog: document.getElementById("task-dialog"),
  taskForm: document.getElementById("task-form"),
  taskDialogTitle: document.getElementById("task-dialog-title"),
  taskName: document.getElementById("task-name"),
  taskProfile: document.getElementById("task-profile"),
  taskSitePicker: document.getElementById("task-site-picker"),
  taskSiteAddButton: document.getElementById("task-site-add-button"),
  taskSiteList: document.getElementById("task-site-list"),
  taskCapabilityTestButton: document.getElementById("task-capability-test-button"),
  taskCapabilityResult: document.getElementById("task-capability-result"),
  siteTestAllButton: document.getElementById("site-test-all-button"),
  siteTestAllResults: document.getElementById("site-test-all-results"),
  batchTestType: document.getElementById("batch-test-type"),
  batchTestRun: document.getElementById("batch-test-run"),
  batchTestSites: document.getElementById("batch-test-sites"),
  batchTestState: document.getElementById("batch-test-state"),
  batchTestResults: document.getElementById("batch-test-results"),
  taskOutputFormat: document.getElementById("task-output-format"),
  taskUmos: document.getElementById("task-umos"),
  taskScheduleNone: document.getElementById("task-schedule-none"),
  taskScheduleDaily: document.getElementById("task-schedule-daily"),
  taskScheduleWeekly: document.getElementById("task-schedule-weekly"),
  taskScheduleInterval: document.getElementById("task-schedule-interval"),
  taskScheduleCron: document.getElementById("task-schedule-cron"),
  taskCronFields: document.getElementById("task-cron-fields"),
  taskScheduleExpression: document.getElementById("task-schedule-expression"),
  taskIntervalFields: document.getElementById("task-interval-fields"),
  taskScheduleStartAt: document.getElementById("task-schedule-start-at"),
  taskScheduleIntervalMinutes: document.getElementById("task-schedule-interval-minutes"),
  taskWeekdays: document.getElementById("task-weekdays"),
  taskScheduleFields: document.getElementById("task-schedule-fields"),
  taskScheduleTime: document.getElementById("task-schedule-time"),
  taskTimeField: document.getElementById("task-time-field"),
  taskScheduleTimezone: document.getElementById("task-schedule-timezone"),
  taskScheduleError: document.getElementById("task-schedule-error"),
  taskScheduleSaved: document.getElementById("task-schedule-saved"),
  taskFormError: document.getElementById("task-form-error"),
  taskWriteUncertain: document.getElementById("task-write-uncertain"),
  taskWriteUncertainText: document.getElementById("task-write-uncertain-text"),
  taskRecheckButton: document.getElementById("task-recheck-button"),
  taskReviewCloseButton: document.getElementById("task-review-close-button"),
  taskCloseDialogButton: document.getElementById("task-close-dialog-button"),
  taskCancelButton: document.getElementById("task-cancel-button"),
  taskSaveButton: document.getElementById("task-save-button"),
  taskDeleteDialog: document.getElementById("task-delete-dialog"),
  taskDeleteMessage: document.getElementById("task-delete-message"),
  taskDeleteError: document.getElementById("task-delete-error"),
  taskDeleteUncertain: document.getElementById("task-delete-uncertain"),
  taskDeleteUncertainText: document.getElementById("task-delete-uncertain-text"),
  taskDeleteRecheckButton: document.getElementById("task-delete-recheck-button"),
  taskDeleteReviewCloseButton: document.getElementById("task-delete-review-close-button"),
  taskDeleteCancelButton: document.getElementById("task-delete-cancel-button"),
  taskDeleteConfirmButton: document.getElementById("task-delete-confirm-button"),
  taskPowerDialog: document.getElementById("task-power-dialog"),
  taskPowerTitle: document.getElementById("task-power-title"),
  taskPowerForm: document.getElementById("task-power-form"),
  taskPowerInfo: document.getElementById("task-power-info"),
  taskPowerNote: document.getElementById("task-power-note"),
  taskPowerError: document.getElementById("task-power-error"),
  taskPowerUncertain: document.getElementById("task-power-uncertain"),
  taskPowerUncertainText: document.getElementById("task-power-uncertain-text"),
  taskPowerRecheckButton: document.getElementById("task-power-recheck-button"),
  taskPowerReviewCloseButton: document.getElementById("task-power-review-close-button"),
  taskPowerCloseButton: document.getElementById("task-power-close-button"),
  taskPowerCancelButton: document.getElementById("task-power-cancel-button"),
  taskPowerConfirmButton: document.getElementById("task-power-confirm-button"),
  scheduleRunsState: document.getElementById("schedule-runs-state"),
  scheduleRunsStateText: document.getElementById("schedule-runs-state-text"),
  scheduleRunsRetryButton: document.getElementById("schedule-runs-retry-button"),
  scheduleRunsRefreshButton: document.getElementById("schedule-runs-refresh-button"),
  scheduleRunsMoreButton: document.getElementById("schedule-runs-more-button"),
  scheduleClaimsList: document.getElementById("schedule-claims-list"),
  scheduleSkipsList: document.getElementById("schedule-skips-list"),
  scheduleClaimsEmpty: document.getElementById("schedule-claims-empty"),
  scheduleSkipsEmpty: document.getElementById("schedule-skips-empty"),
  deliveryList: document.getElementById("delivery-list"),
  deliveryListState: document.getElementById("delivery-list-state"),
  deliveryListStateText: document.getElementById("delivery-list-state-text"),
  deliveryRefreshButton: document.getElementById("delivery-refresh-button"),
  deliveryPendingPanel: document.getElementById("delivery-pending-panel"),
  deliveryPendingText: document.getElementById("delivery-pending-text"),
  deliveryShowPendingButton: document.getElementById("delivery-show-pending-button"),
  deliveryPendingList: document.getElementById("delivery-pending-list"),
  deliveryClearPendingButton: document.getElementById("delivery-clear-pending-button"),
  deliveryDialog: document.getElementById("delivery-dialog"),
  deliveryDialogTitle: document.getElementById("delivery-dialog-title"),
  deliveryForm: document.getElementById("delivery-form"),
  deliveryConfirmInfo: document.getElementById("delivery-confirm-info"),
  deliveryTargets: document.getElementById("delivery-targets"),
  deliveryError: document.getElementById("delivery-error"),
  deliveryResult: document.getElementById("delivery-result"),
  deliveryUncertain: document.getElementById("delivery-uncertain"),
  deliveryUncertainText: document.getElementById("delivery-uncertain-text"),
  deliveryExecutionId: document.getElementById("delivery-execution-id"),
  deliveryConfirmButton: document.getElementById("delivery-confirm-button"),
  deliveryCancelButton: document.getElementById("delivery-cancel-button"),
  deliveryCloseButton: document.getElementById("delivery-close-button"),
  deliveryQueryButton: document.getElementById("delivery-query-button"),
  deliveryLoadMoreButton: document.getElementById("delivery-load-more-button"),
};

const state = {
  batchTestRequestId: 0,
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
  profilePreviewSiteId: null,
  profilePreviewOutputs: null,
  profilePreviewFormat: "text",
  profileOutputEpoch: 0,
  profileCopyOperationId: 0,
  profileImageSnapshotId: null,
  profileImageRequestId: 0,
  profileImageLoading: false,
  imageViewerSection: null,
  imageViewerTrigger: null,
  imageViewerScrollY: 0,
  imageViewerActual: false,
  customBaseUrl: "",
  credentialAutoCleared: false,
  tasks: [],
  taskMode: "create",
  taskSites: [],
  editingTaskId: null,
  editingTaskRevision: null,
  taskDeleteTarget: null,
  taskSaving: false,
  taskDeleting: false,
  taskLoadRequestId: 0,
  taskSaveRequestId: 0,
  taskPending: null,
  taskPowerTarget: null,
  taskPowerMode: "enable",
  taskPowerSaving: false,
  taskPowerPending: null,
  taskPowerRequestId: 0,
  scheduleClaims: [],
  scheduleSkips: [],
  scheduleRunsOffset: 0,
  scheduleRunsHasMore: false,
  scheduleRunsRequestId: 0,
  scheduleRunsRetryReset: true,
  editingTaskSchedule: null,
  editingTaskScheduleRaw: null,
  editingTaskScheduleError: null,
  editingTaskScheduleDirty: false,
  taskFormDisabled: false,
  deliveryTask: null,
  deliveryPreview: null,
  deliveryBusy: false,
  deliveryRequestId: 0,
  deliveryLoadRequestId: 0,
  deliveryPending: null,
  deliveryResult: null,
  deliveryMode: "confirm",
  deliveryPreviewRequestId: 0,
  deliveryQueryRequestId: 0,
  deliveryQueryTarget: null,
  deliveryQueryHistory: false,
  deliveryDialogGeneration: 0,
  deliveryHistoryExecutionId: null,
  deliveryAttempt: null,
  deliveryHistory: [],
  deliveryOffset: 0,
  deliveryHasMore: false,
  deliveryPendingMemory: null,
  deliveryStorageAvailable: true,
  deliveryPendingHiddenFor: null,
};

const TASK_OUTPUT_LABELS = {
  text: "纯文字",
  markdown: "Markdown",
  image: "图片",
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
  /仍是任务的候选站点/,
  /内容组合不存在/,
  /内容组合存储暂不可用/,
  /时区数据不可用/,
  /确认的配置版本已变化/,
  /投递容量已满/,
  /当前无法开始投递/,
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

function isInactiveMessage(message) {
  return message.includes("未启用") || message.includes("插件不可用");
}

function isDuplicateNameMessage(message) {
  return message.includes("已存在同名站点");
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
    "input, select, textarea, button",
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
  captureFormBaselines(dialog);
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

function setSourceKindHint(visible) {
  const hint = elements.sourceKindHint;
  if (!hint) {
    return;
  }
  hint.hidden = !visible;
  elements.sourceKind.setAttribute(
    "aria-describedby",
    visible ? "source-kind-hint" : "",
  );
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
    // 只接受明确取值；缺失或未知保持 null，交给界面显式提示，
    // 不把无法确认的来源类型默认成“公开服务”。
    source_kind:
      raw.source_kind === "public" || raw.source_kind === "self_built"
        ? raw.source_kind
        : null,
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

let siteTestDisplayToken = 0;
let siteTestSite = null;
const siteTestPending = new Map();
const SAFE_TEST_ERRORS = new Map([
  ["站点配置已变化，请刷新后重试。", ["站点配置已变化，请关闭后刷新列表再重试。", "warning"]],
  ["同时进行的连接测试过多，请稍后重试。", ["同时进行的连接测试过多，请稍后手动重试。", "warning"]],
  ["测试连接超时。", ["所选接口连接超时，请稍后手动重试。", "danger"]],
  ["插件当前不可用。", ["插件当前不可用，请关闭后重新打开页面。", "danger"]],
  ["插件当前不可用，请重新打开页面后重试。", ["插件当前不可用，请重新打开页面后重试。", "danger"]],
  ["内容客户端当前不可用。", ["内容客户端当前不可用，请稍后手动重试。", "danger"]],
  ["站点存储不可用。", ["站点存储当前不可用，请稍后手动重试。", "danger"]],
  ["需要登录后才能获取内容预览。", ["登录状态无效，请重新登录 Dashboard。", "danger"]],
  ["连接上游失败。", ["无法连接上游，请检查站点地址后手动重试。", "danger"]],
  ["网络传输失败。", ["网络传输失败，请检查站点地址后手动重试。", "danger"]],
  ["上游返回了失败状态。", ["上游返回了失败状态。", "danger"]],
  ["插件当前未启用。", ["插件当前未启用，请启用插件后重新打开页面。", "danger"]],
  ["插件当前不可用。", ["插件当前不可用，请关闭后重新打开页面。", "danger"]],
  ["插件当前不可用，请重新打开页面后重试。", ["插件当前不可用，请重新打开页面后重试。", "danger"]],
  ["站点不存在。", ["站点不存在或已被删除，请刷新列表。", "danger"]],
  ["站点不存在或已被删除。", ["站点不存在或已被删除，请刷新列表。", "danger"]],
  ["内容类型无效。", ["所选内容类型无效。", "danger"]],
  ["revision 无效。", ["站点版本无效，请刷新列表。", "danger"]],
  ["测试参数无效。", ["测试参数无效，请刷新列表后重试。", "danger"]],
  ["请求体必须是 JSON 对象。", ["请求格式无效。", "danger"]],
  ["需要登录后才能获取内容预览。", ["登录状态无效，请重新登录 Dashboard。", "danger"]],
  ["上游发生不允许的重定向。", ["上游返回了不允许的重定向。", "danger"]],
  ["上游响应过大。", ["上游响应超过大小限制。", "danger"]],
  ["上游返回非 JSON。", ["上游返回的内容不是有效 JSON。", "danger"]],
  ["上游业务状态无效。", ["上游业务状态无效。", "danger"]],
  ["上游内容结构无效。", ["上游内容结构不符合所选接口约定。", "danger"]],
  ["测试失败，无法完成连接验证。", ["测试失败，请稍后手动重试。", "danger"]],
]);
function setSiteTestResult(text, tone) {
  const result = document.getElementById("site-test-result");
  result.hidden = !text;
  result.textContent = text || "";
  result.dataset.tone = tone || "info";
}
function siteTestRunning(site, run) {
  const pending = site ? siteTestPending.get(site.id) : null;
  run.disabled = Boolean(pending);
  run.textContent = pending ? "测试中…" : "开始测试";
}
function openSiteTest(site) {
  siteTestSite = site;
  siteTestDisplayToken += 1;
  document.getElementById("site-test-site").textContent =
    `站点：${site.name}`;
  document.getElementById("site-test-content-type").value = "daily_news";
  elements.siteTestAllResults.hidden = true;
  elements.siteTestAllButton.disabled = false;
  elements.siteTestAllResults.replaceChildren();
  const run = document.getElementById("site-test-run-button");
  siteTestRunning(site, run);
  setSiteTestResult(siteTestPending.has(site.id) ? "正在请求所选接口…" : "",
    siteTestPending.has(site.id) ? "info" : null);
  document.getElementById("site-test-dialog").showModal();
}
async function runSiteTest() {
  const site = siteTestSite;
  if (!site || siteTestPending.has(site.id)) return;
  const content = document.getElementById("site-test-content-type").value;
  const displayToken = siteTestDisplayToken;
  const record = { contentType: content, displayToken };
  siteTestPending.set(site.id, record);
  const run = document.getElementById("site-test-run-button");
  run.disabled = true;
  run.textContent = "测试中…";
  setSiteTestResult("正在请求所选接口…", "info");
  try {
    const data = await bridge.apiPost(`sites/${site.id}/test`, {
      expected_revision: site.revision,
      content_type: content,
    });
    if (!siteTestPending.has(site.id) ||
        siteTestPending.get(site.id) !== record ||
        document.getElementById("site-test-dialog").open !== true ||
        siteTestSite !== site ||
        siteTestDisplayToken !== displayToken ||
        document.getElementById("site-test-content-type").value !== content) return;
    const value = data && data.data ? data.data : data;
    setSiteTestResult(value.result === "empty"
      ? `${value.message} 耗时 ${value.elapsed_ms} ms。`
      : `连接成功：${value.message} 耗时 ${value.elapsed_ms} ms。`, "success");
  } catch (error) {
    if (!siteTestPending.has(site.id) ||
        siteTestPending.get(site.id) !== record ||
        document.getElementById("site-test-dialog").open !== true ||
        siteTestSite !== site ||
        siteTestDisplayToken !== displayToken ||
        document.getElementById("site-test-content-type").value !== content) return;
    const message = error && typeof error.message === "string"
      ? error.message.trim() : "";
    const safe = SAFE_TEST_ERRORS.get(message);
    if (safe) {
      setSiteTestResult(safe[0], safe[1]);
    } else {
      setSiteTestResult("测试失败，请手动重试；未修改站点配置。", "danger");
    }
  } finally {
    if (siteTestPending.get(site.id) === record) siteTestPending.delete(site.id);
    if (document.getElementById("site-test-dialog").open === true) {
      siteTestRunning(siteTestSite, document.getElementById("site-test-run-button"));
      const result = document.getElementById("site-test-result");
      const currentPending = siteTestSite
        ? siteTestPending.get(siteTestSite.id) : null;
      if (!currentPending && siteTestDisplayToken !== displayToken &&
          result.textContent === "正在请求所选接口…") {
        setSiteTestResult(
          "本次测试已结束，但结果未在当前弹窗展示；请手动重新测试。",
          "warning",
        );
      }
    }
  }
}
function closeSiteTest() {
  siteTestDisplayToken += 1;
  siteTestSite = null;
  document.getElementById("site-test-dialog").close();
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
  addMeta(meta, "来源", sourceKindLabel(site.source_kind));
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
      "测试连接",
      "button button-secondary",
      () => openSiteTest(site),
      `测试站点 ${site.name} 连接`,
    ),
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
    renderBatchTestSites();
    syncPreviewSites();
    profilePreviewSiteOptions();
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
  elements.previewSourceUrl.textContent = "";
  elements.previewSourceUrl.removeAttribute("title");
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
  const sourceDisplay =
    typeof snapshot.source_display === "string" && snapshot.source_display
      ? snapshot.source_display
      : "来源不可用";
  elements.previewSourceUrl.textContent = sourceDisplay;
  if (snapshot.source_kind === "public" && sourceDisplay !== "来源不可用") {
    elements.previewSourceUrl.title = sourceDisplay;
  } else {
    elements.previewSourceUrl.removeAttribute("title");
  }
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
    ? `仍在列表中：${site.name}（${
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
  titleLine.append(name);

  const summary = document.createElement("p");
  summary.className = "site-meta profile-summary";
  summary.textContent = profileSummary(profile) || "没有栏目";
  const meta = document.createElement("p");
  meta.className = "site-meta";
  addMeta(meta, "栏目", `${profile.items.length} 项`);

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
  const hasSite = Boolean(elements.profilePreviewSite.value);
  elements.profilePreviewSelect.disabled = busy || !hasProfile;
  elements.profilePreviewSite.disabled = busy || !hasSite;
  elements.profilePreviewButton.disabled = busy || !hasProfile;
  elements.profilePreviewRefreshButton.disabled = busy || !hasProfile;
  elements.profilePreviewButton.textContent = busy ? "正在获取…" : "获取组合";
  elements.profilePreviewRefreshButton.textContent = busy ? "正在刷新…" : "刷新组合";
  syncProfileOutputButtons();
  syncProfileImageButton();
}

function resetProfileImages() {
  state.profileImageRequestId += 1;
  state.profileImageLoading = false;
  state.profileImageSnapshotId = null;
  const hadViewer = Boolean(
    elements.profileImageViewer && elements.profileImageViewer.open,
  );
  elements.profileImagePages.replaceChildren();
  closeProfileImageViewer(false);
  elements.profileImageState.textContent = "";
  elements.profileImageState.hidden = true;
  elements.profileImageHint.textContent = "";
  elements.profileImageHint.hidden = true;
  syncProfileImageButton();
  if (hadViewer) {
    // 浮层因预览刷新被收起时，按钮可能同时被禁用；把焦点交给视图标题，
    // 避免焦点落到 body 或已关闭的浮层里。
    const heading = document.querySelector("#view-profiles h1");
    if (heading) {
      if (!heading.hasAttribute("tabindex")) {
        heading.setAttribute("tabindex", "-1");
      }
      heading.focus({ preventScroll: true });
    }
  }
}

function syncProfileImageButton() {
  const hasImageContext = Boolean(
    state.profilePreviewOutputs &&
    state.profileImageSnapshotId &&
    state.profilePreviewId,
  );
  elements.profileImageButton.disabled =
    state.profilePreviewLoading ||
    state.profileImageLoading ||
    !hasImageContext;
  elements.profileImageButton.textContent =
    state.profileImageLoading ? "正在生成…" : "生成图片";
}

const PROFILE_IMAGE_STATUS_LABELS = {
  success: "已生成",
  empty: "空栏目",
  failed: "未生成",
};

const PROFILE_IMAGE_VIEWER_CLASS = "profile-image-viewer-open";
const PROFILE_IMAGE_VIEWER_HINT =
  "已按屏幕宽度显示完整长图；需要细节时点“原始尺寸”。";
const PROFILE_IMAGE_VIEWER_HINT_ACTUAL =
  "原始 900px 显示，窄屏可左右滑动查看细节。";

function profileImageViewerFocusFallback() {
  const candidates = [
    elements.profileImageButton,
    elements.profilePreviewSelect,
    elements.profilePreviewButton,
  ];
  for (const candidate of candidates) {
    if (candidate && !candidate.disabled && !candidate.hidden) {
      return candidate;
    }
  }
  // 预览进行中时上述控件都可能被禁用，退到当前视图标题，避免焦点落到 body。
  const heading = document.querySelector("#view-profiles h1");
  if (heading) {
    if (!heading.hasAttribute("tabindex")) {
      heading.setAttribute("tabindex", "-1");
    }
    return heading;
  }
  return null;
}

function applyProfileImageViewerZoom(actual) {
  state.imageViewerActual = Boolean(actual);
  const body = elements.profileImageViewerBody;
  if (body) body.classList.toggle("is-actual", state.imageViewerActual);
  const button = elements.profileImageViewerZoom;
  if (button) {
    button.setAttribute("aria-pressed", String(state.imageViewerActual));
    button.textContent = state.imageViewerActual ? "适应宽度" : "原始尺寸";
  }
  elements.profileImageViewerHint.textContent = state.imageViewerActual
    ? PROFILE_IMAGE_VIEWER_HINT_ACTUAL
    : PROFILE_IMAGE_VIEWER_HINT;
}

function openProfileImageViewer(section, trigger) {
  const viewer = elements.profileImageViewer;
  if (!viewer || typeof viewer.showModal !== "function" || viewer.open) {
    return;
  }
  if (!section || typeof section.png_base64 !== "string" || !section.png_base64) {
    return;
  }
  state.imageViewerSection = section;
  state.imageViewerTrigger = trigger || null;
  state.imageViewerScrollY = window.scrollY;
  elements.profileImageViewerTitle.textContent = section.index + ". " + section.label;
  elements.profileImageViewerImg.src = "data:image/png;base64," + section.png_base64;
  elements.profileImageViewerImg.width = section.width;
  elements.profileImageViewerImg.height = section.height;
  elements.profileImageViewerImg.alt = section.label + "栏目长图（大图）";
  applyProfileImageViewerZoom(false);
  elements.profileImageViewerDownload.textContent = "下载「" + section.label + "」PNG";
  elements.profileImageViewerDownload.setAttribute(
    "aria-label",
    "下载" + section.label + "栏目长图",
  );
  document.documentElement.classList.add(PROFILE_IMAGE_VIEWER_CLASS);
  elements.profileImageViewerBody.scrollTop = 0;
  elements.profileImageViewerBody.scrollLeft = 0;
  viewer.showModal();
  elements.profileImageViewerClose.focus();
}

function closeProfileImageViewer(restoreFocus = true) {
  document.documentElement.classList.remove(PROFILE_IMAGE_VIEWER_CLASS);
  const viewer = elements.profileImageViewer;
  if (!viewer || !viewer.open) {
    return;
  }
  const trigger = state.imageViewerTrigger;
  const scrollY = state.imageViewerScrollY;
  state.imageViewerSection = null;
  state.imageViewerTrigger = null;
  viewer.close();
  elements.profileImageViewerImg.removeAttribute("src");
  elements.profileImageViewerImg.alt = "";
  elements.profileImageViewerImg.width = 900;
  elements.profileImageViewerImg.height = 0;
  applyProfileImageViewerZoom(false);
  if (restoreFocus) {
    const target = trigger && document.contains(trigger)
      ? trigger
      : profileImageViewerFocusFallback();
    if (target && typeof target.focus === "function") {
      target.focus({ preventScroll: true });
    }
  }
  if (window.scrollY !== scrollY) {
    window.scrollTo(0, scrollY);
  }
}


/**
 * 编辑站点并把地址换到另一个服务时，旧凭据属于上一个服务。
 * 默认改为“清除凭据”，让管理员显式决定是否继续沿用。
 */
function credentialActionInput(value) {
  return elements.siteForm.querySelector(
    `input[name="credential_action"][value="${value}"]`,
  );
}

/** 系统因跨站自动做的凭据调整可被撤回；管理员自己的选择永远优先。 */
function autoClearCredential(reason) {
  const keep = credentialActionInput("keep");
  const clear = credentialActionInput("clear");
  if (!keep || !clear || !keep.checked) return;
  clear.checked = true;
  state.credentialAutoCleared = true;
  syncCredentialFields(true, "clear");
  elements.credentialState.textContent = reason;
}

function restoreAutoClearedCredential() {
  if (!state.credentialAutoCleared) return;
  const keep = credentialActionInput("keep");
  state.credentialAutoCleared = false;
  if (!keep) return;
  keep.checked = true;
  syncCredentialFields(true, "keep");
}

function handleBaseUrlChanged() {
  if (state.mode !== "edit" || !state.editingHasToken) return;
  const before = state.editingBaseOrigin || "";
  const after = originOf(elements.baseUrl.value);
  if (!before || !after) return;
  if (before !== after) {
    // 跨站：先按“清除”处理，避免旧服务凭据悄悄发给新服务。
    autoClearCredential(
      "服务地址已更换：原凭据属于上一个服务，已默认改为“清除凭据”。如需继续使用，请手动选择“保留原凭据”。",
    );
    return;
  }
  // 回到原服务：撤销系统自动选择的清除，保留管理员自己的选择。
  restoreAutoClearedCredential();
}

bindProfileImageViewer();

function bindProfileImageViewer() {
  const viewer = elements.profileImageViewer;
  if (!viewer) {
    return;
  }
  elements.profileImageViewerClose?.addEventListener("click", () => {
    closeProfileImageViewer(true);
  });
  elements.profileImageViewerZoom?.addEventListener("click", () => {
    applyProfileImageViewerZoom(!state.imageViewerActual);
  });
  elements.profileImageViewerDownload?.addEventListener("click", () => {
    if (state.imageViewerSection) {
      downloadProfileImageSection(state.imageViewerSection);
    }
  });
  viewer.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeProfileImageViewer(true);
  });
  viewer.addEventListener("click", (event) => {
    if (event.target === viewer) {
      closeProfileImageViewer(true);
    }
  });
}

function renderProfileImages(data) {
  if (!data || !Array.isArray(data.images) || data.images.length === 0) {
    throw new Error("图片数据格式异常。");
  }
  elements.profileImagePages.replaceChildren();
  closeProfileImageViewer(true);
  let generated = 0;
  let missing = 0;
  for (const section of data.images) {
    if (
      !section ||
      !Number.isInteger(section.index) ||
      typeof section.content_type !== "string" ||
      typeof section.label !== "string" ||
      !section.label ||
      !PROFILE_IMAGE_STATUS_LABELS[section.status]
    ) {
      throw new Error("栏目长图数据格式异常。");
    }
    const figure = document.createElement("figure");
    figure.className = "profile-image-page";
    figure.dataset.status = section.status;
    const hasImage = section.status !== "failed";
    if (hasImage) {
      if (
        typeof section.png_base64 !== "string" ||
        !Number.isInteger(section.width) ||
        !Number.isInteger(section.height)
      ) {
        throw new Error("栏目长图数据格式异常。");
      }
      const image = document.createElement("img");
      image.src = "data:image/png;base64," + section.png_base64;
      image.alt = section.label + "栏目长图";
      image.width = section.width;
      image.height = section.height;
      image.loading = "lazy";
      image.tabIndex = 0;
      image.setAttribute("role", "button");
      image.setAttribute("aria-haspopup", "dialog");
      image.setAttribute("aria-label", "查看「" + section.label + "」大图");
      image.title = "点击查看大图";
      image.addEventListener("click", () => {
        openProfileImageViewer(section, image);
      });
      image.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
          event.preventDefault();
          openProfileImageViewer(section, image);
        }
      });
      figure.append(image);
      generated += 1;
    } else {
      missing += 1;
    }
    const caption = document.createElement("figcaption");
    if (hasImage) {
      caption.textContent =
        section.index + ". " + section.label + " · " +
        section.width + "×" + section.height + " · " +
        PROFILE_IMAGE_STATUS_LABELS[section.status];
    } else {
      caption.className = "task-disabled";
      const reason = typeof section.error === "string" && section.error
        ? section.error
        : "未生成";
      caption.textContent =
        section.index + ". " + section.label + " · 未生成（" + reason + "）";
    }
    figure.append(caption);
    if (hasImage) {
      const actions = document.createElement("div");
      actions.className = "profile-image-actions";
      const download = makeButton(
        "下载「" + section.label + "」PNG",
        "button button-secondary",
        () => downloadProfileImageSection(section),
        "下载" + section.label + "栏目长图",
      );
      const view = makeButton(
        "查看大图",
        "button button-secondary",
        () => openProfileImageViewer(section, view),
        "查看" + section.label + "大图",
      );
      actions.append(download, view);
      figure.append(actions);
    }
    elements.profileImagePages.append(figure);
  }
  elements.profileImageState.textContent = missing
    ? "已生成 " + generated + " 张栏目长图，" + missing + " 个栏目未生成"
    : "已生成 " + generated + " 张栏目长图";
  elements.profileImageState.hidden = false;
  elements.profileImageHint.textContent = missing
    ? "未生成的栏目没有可用图片；如需完整出图，请减少栏目条数后重新预览再生成。"
    : "";
  elements.profileImageHint.hidden = !missing;
}

function downloadProfileImageSection(section) {
  try {
    const raw = atob(section.png_base64);
    const bytes = new Uint8Array(raw.length);
    for (let index = 0; index < raw.length; index += 1) {
      bytes[index] = raw.charCodeAt(index);
    }
    if (bytes[0] !== 0x89 || bytes[1] !== 0x50 ||
        bytes[2] !== 0x4e || bytes[3] !== 0x47) {
      throw new Error("not a png");
    }
    const safeId = String(state.profilePreviewId || "profile")
      .replace(/[^A-Za-z0-9_-]/g, "")
      .slice(0, 64) || "profile";
    const safeType = String(section.content_type || "section")
      .replace(/[^A-Za-z0-9_-]/g, "")
      .slice(0, 32) || "section";
    const filename =
      "60sapi-profile-" + safeId + "-" + safeType + "-" + section.index + ".png";
    const blob = new Blob([bytes], { type: "image/png" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.rel = "noopener";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    elements.profileImageHint.textContent = "已下载 " + filename + "。";
    elements.profileImageHint.hidden = false;
  } catch {
    elements.profileImageHint.textContent = "图片下载失败，请重新生成。";
    elements.profileImageHint.hidden = false;
  }
}

async function generateProfileImage() {
  if (
    !bridge ||
    state.profileImageLoading ||
    state.profilePreviewLoading ||
    !state.profileImageSnapshotId ||
    !state.profilePreviewOutputs
  ) {
    return;
  }
  const profileId = state.profilePreviewId;
  const snapshotId = state.profileImageSnapshotId;
  const requestId = ++state.profileImageRequestId;
  state.profileImageLoading = true;
  elements.profileImagePages.replaceChildren();
  elements.profileImageHint.textContent = "";
  elements.profileImageHint.hidden = true;
  elements.profileImageState.textContent = "正在生成图片…";
  elements.profileImageState.hidden = false;
  syncProfileImageButton();
  try {
    const data = await bridge.apiPost(
      `profiles/${encodeURIComponent(profileId)}/image`,
      { snapshot_id: snapshotId },
    );
    if (
      requestId !== state.profileImageRequestId ||
      snapshotId !== state.profileImageSnapshotId ||
      profileId !== state.profilePreviewId ||
      !state.profilePreviewOutputs
    ) {
      return;
    }
    renderProfileImages(data);
  } catch (error) {
    if (requestId === state.profileImageRequestId) {
      elements.profileImageState.textContent = "";
      elements.profileImageState.hidden = true;
      elements.profileImageHint.textContent =
        `图片生成失败：${friendlyErrorMessage(error)}；文字和 Markdown 仍可使用。`;
      elements.profileImageHint.hidden = false;
    }
  } finally {
    if (requestId === state.profileImageRequestId) {
      state.profileImageLoading = false;
      syncProfileImageButton();
    }
  }
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
  resetProfileImages();
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

function profilePreviewSiteOptions() {
  const select = elements.profilePreviewSite;
  select.replaceChildren();
  if (state.sites.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "没有站点";
    select.append(option);
    select.disabled = true;
    state.profilePreviewSiteId = null;
    return;
  }
  for (const site of state.sites) {
    const option = document.createElement("option");
    option.value = site.id;
    option.textContent = site.enabled ? site.name : `${site.name}（已停用）`;
    select.append(option);
  }
  if (!state.sites.some((site) => site.id === state.profilePreviewSiteId)) {
    // 预览只接受启用站点，但允许显式选中停用站点看后端拒绝原因。
    const preferred = state.sites.find((site) => site.enabled) || state.sites[0];
    state.profilePreviewSiteId = preferred.id;
  }
  select.value = state.profilePreviewSiteId;
  select.disabled = state.profilePreviewLoading;
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
  clearProfileFormError();
  setProfileFormDisabled(false);
  elements.profileSaveButton.textContent = "保存组合";
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
  state.profileSaving = true;
  const idleLabel = state.profileMode === "create" ? "保存组合" : "保存更改";
  elements.profileSaveButton.textContent = "保存中…";
  setProfileFormDisabled(true);
  try {
    const payload = {
      name: elements.profileName.value,
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
  const sourceDisplay =
    typeof section.source_display === "string" && section.source_display
      ? section.source_display
      : "来源不可用";
  const sourceItem = document.createElement("span");
  sourceItem.className = "meta-source";
  const sourceLabel = document.createElement("strong");
  sourceLabel.textContent = "数据来源 ";
  const sourceValue = document.createElement("span");
  sourceValue.textContent = sourceDisplay;
  if (section.source_kind === "public" && sourceDisplay !== "来源不可用") {
    sourceValue.title = sourceDisplay;
  }
  sourceItem.append(sourceLabel, sourceValue);
  meta.append(sourceItem);
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
  resetProfileImages();
  const snapshot = data.image_snapshot && typeof data.image_snapshot === "object"
    ? data.image_snapshot
    : null;
  state.profileImageSnapshotId =
    snapshot && typeof snapshot.id === "string" && snapshot.id
      ? snapshot.id
      : null;
  if (!state.profileImageSnapshotId) {
    elements.profileImageHint.textContent =
      "本次预览未保留图片快照，可能超过大小限制；文字和 Markdown 仍可使用。";
    elements.profileImageHint.hidden = false;
  }
  syncProfileImageButton();
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
  const siteId = elements.profilePreviewSite.value;
  if (!siteId) {
    showProfilePreviewStatus("请先选择本次预览使用的站点。", "error");
    return;
  }
  state.profilePreviewId = profileId;
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
      { force_refresh: Boolean(forceRefresh), site_id: siteId },
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
      elements.profilePreviewSite.value !== siteId ||
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
  elements.sourceKind.value = SOURCE_KIND_PUBLIC;
  elements.instancePreset.value = "custom";
  state.customBaseUrl = "";
  state.credentialAutoCleared = false;
  setSourceKindHint(false);
  syncInstanceTypeUI();
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
  state.customBaseUrl = site.base_url;
  state.editingBaseOrigin = originOf(site.base_url);
  state.editingHasToken = Boolean(site.has_bearer_token);
  state.credentialAutoCleared = false;
  elements.timeout.value = String(site.timeout_seconds);
  elements.enabled.checked = site.enabled;
  const known = isKnownSourceKind(site.source_kind);
  elements.sourceKind.value = known
    ? site.source_kind
    : SOURCE_KIND_UNKNOWN;
  // 地址与预设完全一致才回显预设；否则按自定义显示，绝不用预设覆盖已有地址。
  elements.instancePreset.value =
    site.source_kind === SOURCE_KIND_PUBLIC ? presetUrlOf(site.base_url) : "custom";
  setSourceKindHint(!known);
  syncInstanceTypeUI();
  elements.baseUrl.value = site.base_url;
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
  const payload = {
    name: elements.name.value,
    base_url: elements.baseUrl.value,
    enabled: elements.enabled.checked,
    timeout_seconds: Number(elements.timeout.value),
  };
  const kind = elements.sourceKind.value;
  if (isKnownSourceKind(kind)) {
    payload.source_kind = kind;
  } else if (state.mode === "create") {
    // 新建站点默认公开；未知选项只在编辑既有站点时出现。
    payload.source_kind = SOURCE_KIND_PUBLIC;
  }
  // 编辑且来源类型未知时省略该字段：后端按“省略即保持原值”处理，
  // 不会把推断出的公开写回用户的数据。
  return payload;
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
    } else if (isDuplicateNameMessage(message)) {
      showFormError(message);
      elements.name.focus();
      elements.name.select();
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


function taskProfileOptions(selectedId) {
  elements.taskProfile.replaceChildren();
  if (state.profiles.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "没有已保存组合";
    elements.taskProfile.append(option);
    elements.taskProfile.disabled = true;
    return;
  }
  elements.taskProfile.disabled = false;
  for (const profile of state.profiles) {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.name;
    if (profile.id === selectedId) {
      option.selected = true;
    }
    elements.taskProfile.append(option);
  }
  if (!selectedId && state.profiles.length > 0) {
    elements.taskProfile.value = state.profiles[0].id;
  }
}

const SCHEDULE_ERROR_LABELS = {
  schedule_timezone_unavailable: "时区数据不可用，无法计算理论下次时间。",
  schedule_invalid: "已保存计划格式异常，无法计算理论下次时间。",
  schedule_calculation_error: "当前计划在搜索范围内没有可用时间，无法计算或启用。",
};

function scheduleLabel(task) {
  const saved = task.schedule || task.schedule_raw;
  if (saved && saved.kind === "daily") {
    return `计划：每日 ${saved.time || "?"} · ${saved.timezone || "?"}`;
  }
  if (saved && saved.kind === "cron") {
    return `计划：Cron ${saved.expression} · ${saved.timezone}`;
  }
  if (saved && saved.kind === "interval") {
    return `计划：每 ${saved.interval_minutes} 分钟 · ${saved.start_at} · ${saved.timezone}`;
  }
  if (saved && saved.kind === "weekly") {
    if (!Array.isArray(saved.weekdays)) {
      return "计划：已保存，但星期配置异常";
    }
    return `计划：每周 ${saved.weekdays.join(",")} ${saved.time || "?"} · ${saved.timezone || "?"}`;
  }
  if (saved && typeof saved === "object") {
    return "计划：已保存，但格式异常";
  }
  return "计划：无计划";
}

function taskScheduleError(task) {
  return task.schedule_error || task.schedule_preview?.error || null;
}

function schedulePreviewText(task) {
  if (!task.schedule && !task.schedule_raw) {
    return "";
  }
  const error = taskScheduleError(task);
  if (error) {
    return SCHEDULE_ERROR_LABELS[error] ||
      "理论下次时间不可计算。";
  }
  const preview = task.schedule_preview;
  if (!preview || !preview.next_run_at) {
    return "理论下次时间暂不可用。";
  }
  const note = task.enabled
    ? "任务已启用；实际执行见实际下次时间"
    : "任务尚未启用";
  return `理论下次时间（服务端 UTC，${note}）：${preview.next_run_at}` +
    `；评估于 ${preview.evaluated_at}`;
}

function applyTaskScheduleEnabled() {
  const off = state.taskFormDisabled || elements.taskScheduleFields.hidden;
  const mode = currentTaskScheduleMode();
  elements.taskScheduleTime.disabled = off || (mode !== "daily" && mode !== "weekly");
  elements.taskTimeField.hidden = mode !== "daily" && mode !== "weekly";
  elements.taskTimeField.hidden = mode !== "daily" && mode !== "weekly";
  elements.taskScheduleTimezone.disabled = off;
  elements.taskScheduleStartAt.disabled = off || mode !== "interval";
  elements.taskScheduleIntervalMinutes.disabled = off || mode !== "interval";
  elements.taskScheduleExpression.disabled = off || mode !== "cron";
  for (const input of elements.taskWeekdays.querySelectorAll("input")) {
    input.disabled = off || mode !== "weekly";
  }
}

function setTaskScheduleMode(mode) {
  const daily = mode === "daily";
  const weekly = mode === "weekly";
  const interval = mode === "interval";
  const cron = mode === "cron";
  elements.taskScheduleNone.checked = mode === "none";
  elements.taskScheduleDaily.checked = daily;
  elements.taskScheduleWeekly.checked = weekly;
  elements.taskScheduleInterval.checked = interval;
  elements.taskScheduleCron.checked = cron;
  elements.taskScheduleFields.hidden = mode === "none";
  elements.taskWeekdays.hidden = !weekly;
  elements.taskIntervalFields.hidden = !interval;
  elements.taskCronFields.hidden = !cron;
  if ((daily || weekly) && !elements.taskScheduleTime.value) {
    elements.taskScheduleTime.value = "08:30";
  }
  if ((daily || weekly) && !elements.taskScheduleTimezone.value) {
    elements.taskScheduleTimezone.value = "Asia/Shanghai";
  }
  applyTaskScheduleEnabled();
}

function currentTaskScheduleMode() {
  if (elements.taskScheduleCron.checked) return "cron";
  if (elements.taskScheduleInterval.checked) return "interval";
  if (elements.taskScheduleWeekly.checked) return "weekly";
  if (elements.taskScheduleDaily.checked) return "daily";
  return "none";
}

function resetTaskScheduleEditor() {
  state.editingTaskSchedule = null;
  state.editingTaskScheduleRaw = null;
  state.editingTaskScheduleError = null;
  state.editingTaskScheduleDirty = false;
  elements.taskScheduleTime.value = "08:30";
  elements.taskScheduleTimezone.value = "Asia/Shanghai";
  elements.taskScheduleStartAt.value = "";
  elements.taskScheduleIntervalMinutes.value = "";
  for (const input of elements.taskWeekdays.querySelectorAll("input")) {
    input.checked = false;
  }
  elements.taskScheduleError.textContent = "";
  elements.taskScheduleError.hidden = true;
  elements.taskScheduleSaved.textContent = "";
  elements.taskScheduleSaved.hidden = true;
  setTaskScheduleMode("none");
}

function fillTaskScheduleEditor(task) {
  resetTaskScheduleEditor();
  state.editingTaskSchedule = task.schedule || null;
  state.editingTaskScheduleRaw = task.schedule_raw || null;
  state.editingTaskScheduleError = task.schedule_error || null;
  const saved = task.schedule || task.schedule_raw;
  if (saved) {
    setTaskScheduleMode(saved.kind === "cron" ? "cron" : saved.kind === "interval" ? "interval" : saved.kind === "weekly" ? "weekly" : "daily");
    if (typeof saved.time === "string") {
      elements.taskScheduleTime.value = saved.time;
    }
    if (typeof saved.timezone === "string") {
      elements.taskScheduleTimezone.value = saved.timezone;
    }
    if (saved.kind === "cron") {
      elements.taskScheduleExpression.value = saved.expression || "";
    }
    if (saved.kind === "interval") {
      elements.taskScheduleStartAt.value = saved.start_at || "";
      elements.taskScheduleIntervalMinutes.value = saved.interval_minutes || "";
    }
    if (Array.isArray(saved.weekdays)) {
      for (const input of elements.taskWeekdays.querySelectorAll("input")) {
        input.checked = saved.weekdays.includes(Number(input.value));
      }
    }
  }
  const scheduleError = taskScheduleError(task);
  if (scheduleError) {
    elements.taskScheduleError.textContent =
      SCHEDULE_ERROR_LABELS[scheduleError] ||
      "已保存计划暂时不可计算；未修改计划时将原样保留。";
    elements.taskScheduleError.hidden = false;
    elements.taskScheduleSaved.hidden = false;
    elements.taskScheduleSaved.textContent =
      "已保存的原始计划仍保留；修改其他字段不会清除它。";
  } else {
    const preview = schedulePreviewText(task);
    if (preview) {
      elements.taskScheduleSaved.textContent = preview;
      elements.taskScheduleSaved.hidden = false;
    }
  }
}

function markTaskScheduleDirty() {
  state.editingTaskScheduleDirty = true;
  elements.taskScheduleError.hidden = true;
  elements.taskScheduleError.textContent = "";
  elements.taskScheduleSaved.hidden = false;
  elements.taskScheduleSaved.textContent =
    "计划已修改，保存后更新理论时间；保存前不显示旧计划预览。";
}

function collectTaskSchedule() {
  if (!state.editingTaskScheduleDirty) {
    return undefined;
  }
  const mode = currentTaskScheduleMode();
  if (mode === "none") {
    return null;
  }
  const time = elements.taskScheduleTime.value.trim();
  const timezone = elements.taskScheduleTimezone.value.trim();
  if (!timezone) {
    throw new Error("定时计划必须填写 IANA 时区。");
  }
  if (mode === "daily" || mode === "weekly") {
    if (!/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(time)) {
      throw new Error("计划时间必须是 HH:mm 24 小时制。");
    }
  }
  if (mode === "daily") {
    return { kind: "daily", timezone, time };
  }
  if (mode === "cron") {
    const expression = elements.taskScheduleExpression.value.trim().replace(/\s+/g, " ");
    if (!expression) throw new Error("Cron 计划必须填写表达式。");
    return { kind: "cron", timezone, expression };
  }
  if (mode === "interval") {
    const startAt = elements.taskScheduleStartAt.value;
    const minutes = Number(elements.taskScheduleIntervalMinutes.value);
    if (!startAt) throw new Error("间隔计划必须填写本地起点。");
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 10080) {
      throw new Error("间隔必须是 1～10080 的整数分钟。");
    }
    return { kind: "interval", timezone, start_at: startAt, interval_minutes: minutes };
  }
  const weekdays = [...elements.taskWeekdays.querySelectorAll("input")]
    .filter((input) => input.checked)
    .map((input) => Number(input.value))
    .sort((a, b) => a - b);
  if (weekdays.length === 0) {
    throw new Error("每周计划至少选择一个星期。");
  }
  return { kind: "weekly", timezone, time, weekdays };
}

function renderTasks() {
  elements.taskCount.textContent = `${state.tasks.length} 个`;
  elements.tasksList.replaceChildren();
  elements.tasksList.setAttribute("aria-busy", "false");
  if (state.tasks.length === 0) {
    elements.tasksList.hidden = true;
    elements.taskEmptyState.hidden = false;
    clearTaskListState();
    return;
  }
  elements.taskEmptyState.hidden = true;
  elements.tasksList.hidden = false;
  clearTaskListState();
  for (const task of state.tasks) {
    const card = document.createElement("li");
    card.className = "site-card profile-card";
    card.dataset.taskId = task.id;
    const head = document.createElement("div");
    head.className = "site-card-head";
    const title = document.createElement("h3");
    title.textContent = task.name;
    // 版本号只用于并发校验，不展示给用户。
    head.append(title);

    const meta = document.createElement("div");
    meta.className = "site-meta";
    const profile = document.createElement("span");
    profile.textContent = `组合：${task.profile_name}`;
    const taskCandidates = Array.isArray(task.sites) ? task.sites : [];
    const usableCandidates = taskCandidates.filter(
      (item) => item && item.enabled && item.exists !== false,
    );
    if (taskCandidates.length > 0 && usableCandidates.length === 0) {
      // 与原组合站点无关：候选全不可用时执行会改发异常提示。
      profile.textContent += "（候选站点均不可用，执行时将发送异常提示）";
    }
    const output = document.createElement("span");
    output.textContent = `输出：${task.output_label}`;
    const targets = document.createElement("span");
    targets.textContent = `目标数量：${task.target_count} 个 UMO`;
    const schedule = document.createElement("span");
    schedule.textContent = scheduleLabel(task);
    const preview = document.createElement("span");
    preview.className = "task-schedule-preview";
    preview.textContent = schedulePreviewText(task);
    const runtime = document.createElement("span");
    if (task.next_run_at) {
      runtime.textContent =
        `实际下次执行（服务端 UTC）：${task.next_run_at}`;
      runtime.className = "task-schedule-runtime";
    }
    const disabled = document.createElement("span");
    disabled.className = "task-disabled";
    disabled.textContent = task.enabled
      ? "已启用 · 定时调度"
      : "未启用 · 仅管理员手动测试";
    meta.append(profile, output, schedule, preview, runtime, targets, disabled);

    const actions = document.createElement("div");
    actions.className = "profile-card-actions";
    const edit = makeButton("编辑", "button button-secondary", () =>
      openTaskEditor(task), `编辑推送任务 ${task.name}`);
    const power = makeButton(
      task.enabled ? "停用定时调度" : "启用定时调度",
      "button button-secondary",
      () => openTaskPower(task, task.enabled ? "disable" : "enable"),
      `${task.enabled ? "停用" : "启用"}定时调度 ${task.name}`,
    );
    const test = makeButton("测试投递", "button button-primary", () =>
      openDelivery(task), `手动测试投递 ${task.name}`);
    const remove = makeButton("删除", "button button-danger", () =>
      openTaskDelete(task), `删除推送任务 ${task.name}`);
    power.dataset.taskPower = "1";
    power.disabled = state.taskPowerSaving;
    actions.append(power, test, edit, remove);
    card.append(head, meta, actions);
    elements.tasksList.append(card);
  }
}

function setTaskListState(message, showRetry) {
  elements.taskListStateText.textContent = message;
  elements.taskRetryButton.hidden = !showRetry;
  elements.taskListState.hidden = false;
}

function clearTaskListState() {
  elements.taskListStateText.textContent = "";
  elements.taskRetryButton.hidden = true;
  elements.taskListState.hidden = true;
}

async function loadTasks() {
  if (!bridge) {
    return false;
  }
  const requestId = ++state.taskLoadRequestId;
  elements.tasksList.setAttribute("aria-busy", "true");
  try {
    const result = await bridge.apiGet("tasks");
    if (requestId !== state.taskLoadRequestId) {
      return false;
    }
    const raw = result && Array.isArray(result.tasks) ? result.tasks : [];
    state.tasks = raw.filter((task) => task && typeof task.id === "string");
    renderTasks();
    if (state.scheduleClaims.length || state.scheduleSkips.length) {
      renderScheduleRuns();
    }
    return true;
  } catch (error) {
    if (requestId !== state.taskLoadRequestId) {
      return false;
    }
    if (state.tasks.length === 0) {
      elements.tasksList.hidden = true;
      elements.taskEmptyState.hidden = true;
      setTaskListState("无法读取推送任务。请确认插件已启用，然后重试。", true);
    }
    showStatus(`读取推送任务失败：${friendlyErrorMessage(error)}`, "error");
    return false;
  } finally {
    if (requestId === state.taskLoadRequestId) {
      elements.tasksList.setAttribute("aria-busy", "false");
    }
  }
}

function clearTaskFormError() {
  elements.taskFormError.textContent = "";
  elements.taskFormError.hidden = true;
}

function showTaskFormError(message) {
  elements.taskFormError.textContent = message;
  elements.taskFormError.hidden = false;
}

function setTaskFormDisabled(disabled) {
  state.taskFormDisabled = disabled;
  elements.taskSaveButton.disabled = disabled;
  for (const control of elements.taskForm.querySelectorAll(
    "input, select, textarea, button",
  )) {
    control.disabled = disabled;
  }
  applyTaskScheduleEnabled();
  elements.taskForm.setAttribute("aria-busy", String(disabled));
  if (!disabled) updateTaskCapabilityGate();
}

const TASK_SITE_MAX = 5;

function siteNameById(siteId) {
  const site = state.sites.find((item) => item.id === siteId);
  return site ? site.name : "已删除的站点";
}

function taskSiteOptions() {
  elements.taskSitePicker.replaceChildren();
  const available = state.sites.filter(
    (site) => !state.taskSites.includes(site.id),
  );
  if (available.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = state.sites.length === 0 ? "没有已保存站点" : "已全部添加";
    elements.taskSitePicker.append(option);
    elements.taskSitePicker.disabled = true;
    elements.taskSiteAddButton.disabled = true;
    return;
  }
  elements.taskSitePicker.disabled = false;
  elements.taskSiteAddButton.disabled = state.taskSites.length >= TASK_SITE_MAX;
  for (const site of available) {
    const option = document.createElement("option");
    option.value = site.id;
    option.textContent = site.enabled ? site.name : `${site.name}（已停用）`;
    elements.taskSitePicker.append(option);
  }
}


const BATCH_TEST_TYPES = [
  "daily_news", "bilibili_hot", "zhihu_hot", "douyin_hot", "toutiao_hot",
];
const BATCH_RESULT_LABELS = {
  success: "可用", empty: "可用（空内容）", error: "不可用",
};

function batchTestSignature() {
  // 站点配置或所选站点变化都让在途结果失效，避免结果对不上当前选择。
  const sites = state.sites.map(
    (site) => `${site.id}:${site.revision}:${site.enabled}`,
  ).join(",");
  return `${sites}|${batchTestSelected().join(",")}`;
}

function renderBatchTestSites() {
  const box = elements.batchTestSites;
  if (!box) return;
  box.replaceChildren();
  if (state.sites.length === 0) {
    const empty = document.createElement("p");
    empty.className = "help";
    empty.textContent = "还没有已保存站点。";
    box.append(empty);
    elements.batchTestRun.disabled = true;
    return;
  }
  for (const site of state.sites) {
    const label = document.createElement("label");
    label.className = "batch-test-site";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = site.id;
    input.checked = Boolean(site.enabled);
    const text = document.createElement("span");
    text.textContent = site.enabled ? site.name : `${site.name}（已停用）`;
    label.append(input, text);
    box.append(label);
  }
}

function batchTestSelected() {
  return Array.from(
    elements.batchTestSites.querySelectorAll("input[type=checkbox]:checked"),
  ).map((input) => input.value);
}

function renderBatchTestResults(results) {
  const list = elements.batchTestResults;
  list.replaceChildren();
  list.hidden = false;
  const names = new Map(state.sites.map((site) => [site.id, site.name]));
  for (const item of results) {
    const row = document.createElement("li");
    row.className = "batch-test-result";
    row.dataset.result = item.result;
    const head = document.createElement("strong");
    head.textContent = `${names.get(item.site_id) || item.site_id} · ${item.content_type}`;
    const status = document.createElement("span");
    status.className = "batch-test-status";
    status.textContent = BATCH_RESULT_LABELS[item.result] || "未知";
    const detail = document.createElement("span");
    detail.className = "batch-test-detail";
    detail.textContent = item.message || "";
    row.append(head, status, detail);
    list.append(row);
  }
}

async function runBatchTest() {
  const siteIds = batchTestSelected();
  if (siteIds.length === 0) {
    elements.batchTestState.hidden = false;
    elements.batchTestState.textContent = "请至少选择一个站点。";
    return;
  }
  const type = elements.batchTestType.value;
  const contentTypes = type === "all" ? BATCH_TEST_TYPES.slice() : [type];
  const requestId = ++state.batchTestRequestId;
  const signature = batchTestSignature();
  elements.batchTestRun.disabled = true;
  elements.batchTestState.hidden = false;
  elements.batchTestState.textContent = "正在测试所选站点…";
  elements.batchTestResults.hidden = true;
  try {
    const data = await bridge.apiPost("sites/test-batch", {
      site_ids: siteIds, content_types: contentTypes,
    });
    if (requestId !== state.batchTestRequestId || signature !== batchTestSignature()) {
      return;
    }
    const payload = data && data.data ? data.data : (data || {});
    const results = Array.isArray(payload.results) ? payload.results : [];
    renderBatchTestResults(results);
    const failed = results.filter((item) => item.result === "error").length;
    elements.batchTestState.textContent = results.length === 0
      ? "没有收到测试结果。"
      : `完成 ${results.length} 项，${failed} 项不可用。`;
  } catch (error) {
    if (requestId !== state.batchTestRequestId) return;
    elements.batchTestState.textContent = friendlyErrorMessage(error);
  } finally {
    if (requestId === state.batchTestRequestId) {
      elements.batchTestRun.disabled = false;
    }
  }
}
function renderTaskSites() {
  elements.taskSiteList.replaceChildren();
  if (state.taskSites.length === 0) {
    const empty = document.createElement("li");
    empty.className = "task-site-empty";
    empty.textContent = "还没有候选站点：请至少添加一个，执行时按顺序尝试。";
    elements.taskSiteList.append(empty);
  }
  state.taskSites.forEach((siteId, index) => {
    const row = document.createElement("li");
    row.className = "task-site-row";
    const order = document.createElement("span");
    order.className = "task-site-order";
    order.textContent = String(index + 1);
    const name = document.createElement("span");
    name.className = "task-site-name";
    const site = state.sites.find((item) => item.id === siteId);
    name.textContent = siteNameById(siteId);
    if (!site || !site.enabled) {
      const status = document.createElement("span");
      status.className = "task-site-status";
      status.textContent = site ? "（已停用）" : "（已删除）";
      name.append(status);
    }
    const actions = document.createElement("span");
    actions.className = "task-site-actions";
    const up = makeButton("上移", "button button-secondary", () => moveTaskSite(index, -1),
      "上移候选站点");
    const down = makeButton("下移", "button button-secondary", () => moveTaskSite(index, 1),
      "下移候选站点");
    // 首尾位置明确禁用，避免点了没反应。
    up.disabled = index === 0;
    down.disabled = index === state.taskSites.length - 1;
    actions.append(up, down,
      makeButton("移除", "button button-danger", () => removeTaskSite(index),
        "移除候选站点"),
    );
    row.append(order, name, actions);
    elements.taskSiteList.append(row);
  });
  taskSiteOptions();
  updateTaskCapabilityGate();
}

function addTaskSite() {
  const siteId = elements.taskSitePicker.value;
  if (!siteId || state.taskSites.includes(siteId)) return;
  if (state.taskSites.length >= TASK_SITE_MAX) return;
  state.taskSites.push(siteId);
  renderTaskSites();
}

function moveTaskSite(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= state.taskSites.length) return;
  const [item] = state.taskSites.splice(index, 1);
  state.taskSites.splice(target, 0, item);
  renderTaskSites();
}

function removeTaskSite(index) {
  state.taskSites.splice(index, 1);
  renderTaskSites();
}

function setTaskCapabilityResult(text, tone) {
  const node = elements.taskCapabilityResult;
  if (!text) {
    node.hidden = true;
    node.textContent = "";
    node.className = "site-test-result";
    return;
  }
  node.hidden = false;
  node.textContent = text;
  node.className = `site-test-result site-test-${tone || "info"}`;
}

function savedTaskSites(task) {
  if (!task || !Array.isArray(task.sites)) return [];
  return task.sites.map((item) => (typeof item === "string" ? item : item.id));
}

function taskFormMatchesSaved() {
  if (state.taskMode !== "edit" || !state.editingTask) return false;
  if (elements.taskProfile.value !== state.editingTask.profile_id) return false;
  const saved = savedTaskSites(state.editingTask);
  // 组合不再提供默认站点：候选完全由任务自己决定，空候选不算“没有变化”。
  return saved.join(",") === state.taskSites.join(",");
}

function updateTaskCapabilityGate() {
  const button = elements.taskCapabilityTestButton;
  if (state.taskMode !== "edit" || !state.editingTask) {
    button.disabled = true;
    return;
  }
  const matches = taskFormMatchesSaved();
  button.disabled = !matches;
  if (!matches) {
    state.taskCapabilityGateWarning = true;
    setTaskCapabilityResult(
      "当前修改尚未保存；能力测试针对已保存配置，请先保存再测试。",
      "warning",
    );
  } else if (state.taskCapabilityGateWarning) {
    // 撤销修改后不再残留警告。
    state.taskCapabilityGateWarning = false;
    setTaskCapabilityResult("", null);
  }
}

async function runTaskCapabilityTest() {
  if (state.taskMode !== "edit" || !state.editingTaskId) {
    setTaskCapabilityResult("请先保存任务后再测试。", "error");
    return;
  }
  const taskId = state.editingTaskId;
  const revision = state.editingTaskRevision;
  const sitesSnapshot = state.taskSites.slice().join(",");
  const profileSnapshot = elements.taskProfile.value;
  elements.taskCapabilityTestButton.disabled = true;
  setTaskCapabilityResult("正在检查候选站点…", "info");
  // 切换任务、关闭弹窗或改了候选/组合后，旧请求（成功或失败）都作废。
  const stillCurrent = () => state.taskMode === "edit"
    && state.editingTaskId === taskId
    && state.editingTaskRevision === revision
    && state.taskSites.slice().join(",") === sitesSnapshot
    && elements.taskProfile.value === profileSnapshot;
  try {
    const data = await bridge.apiPost(
      `tasks/${encodeURIComponent(taskId)}/capability-test`, {},
    );
    if (!stillCurrent()) return;
    const payload = data && data.data ? data.data : (data || {});
    const notes = [];
    if (payload.truncated) {
      notes.push(`仅检查前 ${payload.checked_count} 个候选`);
    }
    const ttl = payload.cache && payload.cache.ttl_seconds;
    if (ttl) {
      notes.push(`结果来自 ${Math.round(ttl)} 秒内容缓存，非实时连通性测试`);
    }
    const suffix = notes.length > 0 ? `（${notes.join("；")}）` : "";
    setTaskCapabilityResult((payload.message || "测试完成。") + suffix,
      payload.satisfied ? "success" : "error");
  } catch (error) {
    // 失败同样必须丢弃过期响应，不能覆盖“先保存”等当前提示。
    if (!stillCurrent()) return;
    setTaskCapabilityResult(friendlyErrorMessage(error), "error");
  } finally {
    // 按钮状态始终跟随当前表单，而不是无条件启用。
    updateTaskCapabilityGate();
  }
}

const SITE_TEST_TYPES = [
  ["daily_news", "每日新闻"],
  ["bilibili_hot", "Bilibili 热搜"],
  ["zhihu_hot", "知乎热榜"],
  ["douyin_hot", "抖音热搜"],
  ["toutiao_hot", "今日头条热搜"],
];

function renderSiteTestAll(rows) {
  const list = elements.siteTestAllResults;
  list.replaceChildren();
  list.hidden = false;
  for (const row of rows) {
    const item = document.createElement("li");
    item.className = `site-test-all-row site-test-${row.ok ? "success" : "error"}`;
    const label = document.createElement("span");
    label.textContent = row.label;
    const stateText = document.createElement("span");
    stateText.textContent = row.text;
    item.append(label, stateText);
    list.append(item);
  }
}

async function runSiteTestAll() {
  const site = siteTestSite;
  if (!site) return;
  const displayToken = ++siteTestDisplayToken;
  const alive = () => (
    siteTestDisplayToken === displayToken
    && siteTestSite === site
    && document.getElementById("site-test-dialog").open === true
  );
  elements.siteTestAllButton.disabled = true;
  const rows = SITE_TEST_TYPES.map(([, label]) => ({
    label, ok: false, text: "等待测试…",
  }));
  renderSiteTestAll(rows);
  for (let index = 0; index < SITE_TEST_TYPES.length; index += 1) {
    const [contentType, label] = SITE_TEST_TYPES[index];
    rows[index] = { label, ok: false, text: "测试中…" };
    renderSiteTestAll(rows);
    try {
      const data = await bridge.apiPost(
        `sites/${encodeURIComponent(site.id)}/test`,
        { content_type: contentType, expected_revision: site.revision },
      );
      if (!alive()) return;
      const value = data && data.data ? data.data : data;
      const empty = value.result === "empty";
      rows[index] = {
        label,
        ok: true,
        text: empty
          ? `可用（返回合法空内容）· ${value.elapsed_ms} ms`
          : `可用 · ${value.elapsed_ms} ms`,
      };
    } catch (error) {
      if (!alive()) return;
      const message = error && typeof error.message === "string"
        ? error.message.trim() : "";
      const safe = SAFE_TEST_ERRORS.get(message);
      rows[index] = {
        label,
        ok: false,
        text: `不可用（${safe ? safe[0] : "测试失败，请手动重试"}）`,
      };
    }
    renderSiteTestAll(rows);
  }
  if (!alive()) return;
  elements.siteTestAllButton.disabled = false;
}

function openCreateTask() {
  if (state.taskSaving || state.taskDeleting || state.taskPending || state.taskPowerPending) {
    return;
  }
  if (blockIfPendingWrite()) {
    return;
  }
  clearTaskPendingPanels();
  state.taskMode = "create";
  state.editingTask = null;
  state.editingTaskId = null;
  state.editingTaskRevision = null;
  state.taskSites = [];
  elements.taskForm.reset();
  resetTaskScheduleEditor();
  renderTaskSites();
  setTaskCapabilityResult("", null);
  updateTaskCapabilityGate();
  elements.taskDialogTitle.textContent = "新增推送任务";
  taskProfileOptions(null);
  clearTaskFormError();
  setTaskFormDisabled(false);
  elements.taskSaveButton.textContent = "保存任务";
  if (state.profiles.length === 0) {
    showTaskFormError("请先创建至少一个内容组合。");
    elements.taskSaveButton.disabled = true;
  }
  openDialog(elements.taskDialog);
  elements.taskName.focus();
}

function openTaskEditor(task) {
  if (state.taskSaving || state.taskDeleting || state.taskPending || state.taskPowerPending) {
    return;
  }
  if (blockIfPendingWrite()) {
    return;
  }
  clearTaskPendingPanels();
  state.taskMode = "edit";
  state.editingTaskId = task.id;
  state.editingTaskRevision = task.revision;
  elements.taskDialogTitle.textContent = "编辑推送任务";
  elements.taskName.value = task.name;
  taskProfileOptions(task.profile_id);
  elements.taskOutputFormat.value = task.output_format;
  elements.taskUmos.value = Array.isArray(task.umos) ? task.umos.join("\n") : "";
  state.editingTask = task;
  state.taskSites = Array.isArray(task.sites)
    ? task.sites.map((item) => (typeof item === "string" ? item : item.id))
    : [];
  renderTaskSites();
  setTaskCapabilityResult("", null);
  updateTaskCapabilityGate();
  fillTaskScheduleEditor(task);
  clearTaskFormError();
  setTaskFormDisabled(false);
  elements.taskSaveButton.textContent = "保存更改";
  openDialog(elements.taskDialog);
  elements.taskName.focus();
  elements.taskName.select();
}

function collectTaskUmos() {
  return elements.taskUmos.value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

async function handleTaskSave(event) {
  event.preventDefault();
  if (state.taskSaving || state.taskPending || state.pendingWrite) {
    return;
  }
  clearTaskFormError();
  if (!elements.taskForm.reportValidity()) {
    return;
  }
  if (!elements.taskProfile.value) {
    showTaskFormError("请选择内容组合。");
    return;
  }
  if (state.taskSites.length === 0) {
    // 没有候选站点就没有执行目标，不能保存成看似可用的任务。
    showTaskFormError("请至少添加一个候选站点后再保存。");
    elements.taskSitePicker?.focus();
    return;
  }
  let schedule;
  try {
    schedule = collectTaskSchedule();
  } catch (error) {
    showTaskFormError(friendlyErrorMessage(error));
    return;
  }
  const requestId = ++state.taskSaveRequestId;
  state.taskSaving = true;
  elements.taskSaveButton.textContent = "保存中…";
  setTaskFormDisabled(true);
  const payload = {
    name: elements.taskName.value,
    profile_id: elements.taskProfile.value,
    output_format: elements.taskOutputFormat.value,
    umos: collectTaskUmos(),
  };
  payload.sites = state.taskSites.slice();
  if (schedule !== undefined) {
    payload.schedule = schedule;
  }
  try {
    if (state.taskMode === "create") {
      await bridge.apiPost("tasks", payload);
    } else {
      payload.expected_revision = state.editingTaskRevision;
      await bridge.apiPost(
        `tasks/${encodeURIComponent(state.editingTaskId)}/update`,
        payload,
      );
    }
    if (requestId !== state.taskSaveRequestId) {
      return;
    }
    closeDialog(elements.taskDialog);
    const reloaded = await loadTasks();
    showStatus(
      reloaded ? "推送任务配置已保存；任务已自动停用，需重新启用才能继续调度；未发送消息。" : "任务已保存，但列表刷新失败，请重试。",
      reloaded ? "success" : "error",
    );
  } catch (error) {
    if (requestId !== state.taskSaveRequestId) {
      return;
    }
    const message = friendlyErrorMessage(error);
    if (
      state.taskMode === "edit" &&
      (isConflictMessage(message) || isMissingMessage(message))
    ) {
      closeDialog(elements.taskDialog);
      const reloaded = await loadTasks();
      showStatus(
        reloaded
          ? "该任务已被修改或删除，已重新加载最新数据。请重新编辑。"
          : "该任务已被修改或删除，但列表刷新失败。",
        "error",
      );
    } else if (isExplicitWriteRejection(message)) {
      showTaskFormError(message);
    } else {
      enterTaskPending(
        state.taskMode,
        writeFailureDetail(error),
        state.editingTaskId,
      );
    }
  } finally {
    if (requestId === state.taskSaveRequestId) {
      state.taskSaving = false;
      if (!state.taskPending) {
        setTaskFormDisabled(false);
        elements.taskSaveButton.textContent =
          state.taskMode === "create" ? "保存任务" : "保存更改";
      }
    }
  }
}

function taskPowerPreflight(task, mode) {
  if (mode !== "enable") return null;
  if (!task.schedule || taskScheduleError(task)) {
    return "任务没有可计算的定时计划，不能启用。";
  }
  if (!task.schedule.timezone) {
    return "定时计划缺少 IANA 时区，不能启用。";
  }
  if (task.schedule.kind === "cron") {
    if (!task.schedule.expression) return "Cron 计划缺少表达式，不能启用。";
  } else if (task.schedule.kind === "interval") {
    if (!task.schedule.start_at || !Number.isInteger(task.schedule.interval_minutes)) {
      return "间隔计划缺少起点或有效分钟数，不能启用。";
    }
  } else if (!task.schedule.time) {
    return "定时计划缺少执行时间，不能启用。";
  }
  if (!Array.isArray(task.umos) || task.umos.length === 0) {
    return "任务 UMO 白名单为空，不能启用。";
  }
  // 站点可用性不在这里拦截：候选站点全停用时执行会发送安全异常提示，
  // 与后端规则保持一致；组合原站点停用也不再阻止启用。
  return null;
}

function appendPowerLine(label, value) {
  const row = document.createElement("div");
  const strong = document.createElement("strong");
  strong.textContent = `${label}：`;
  const text = document.createElement("span");
  text.textContent = value;
  row.append(strong, text);
  elements.taskPowerInfo.append(row);
}

function openTaskPower(task, mode) {
  if (state.taskPowerSaving || state.taskPowerPending || state.pendingWrite) {
    return;
  }
  if (blockIfPendingWrite()) {
    return;
  }
  const reason = taskPowerPreflight(task, mode);
  state.taskPowerTarget = task;
  state.taskPowerMode = mode;
  state.taskPowerRequestId += 1;
  elements.taskPowerTitle.textContent = mode === "enable"
    ? "启用定时调度" : "停用定时调度";
  elements.taskPowerConfirmButton.textContent = mode === "enable"
    ? "确认启用" : "确认停用";
  elements.taskPowerInfo.replaceChildren();
  appendPowerLine("任务", task.name);
  appendPowerLine("内容组合", task.profile_name || "未知组合");
  appendPowerLine("输出方式", task.output_label || task.output_format);
  appendPowerLine("目标", Array.isArray(task.umos) && task.umos.length
    ? task.umos.join("\n") : "无");
  appendPowerLine("定时计划", task.schedule
    ? `${task.schedule.kind === "cron" ? "Cron " + task.schedule.expression : task.schedule.kind === "interval" ? "每 " + task.schedule.interval_minutes + " 分钟 · " + task.schedule.start_at : task.schedule.kind === "weekly" ? "每周 " + (task.schedule.weekdays || []).join(",") : "每日"} ${task.schedule.kind === "interval" || task.schedule.kind === "cron" ? "" : task.schedule.time + " · "}${task.schedule.timezone}`
    : "无计划");
  elements.taskPowerNote.textContent = mode === "enable"
    ? "启用后按计划自动向上述目标发送；首次安排严格晚于当前时刻，不会立即投递。任务状态和实际下次时间以服务器返回为准。"
    : "停用会取消未来调度。已进入外部调用的消息可能无法撤回；unknown 结果不会自动重试。";
  elements.taskPowerError.textContent = reason || "";
  elements.taskPowerError.hidden = !reason;
  elements.taskPowerConfirmButton.disabled = Boolean(reason);
  elements.taskPowerUncertain.hidden = true;
  elements.taskPowerRecheckButton.hidden = false;
  elements.taskPowerReviewCloseButton.hidden = true;
  elements.taskPowerCancelButton.disabled = false;
  elements.taskPowerCloseButton.disabled = false;
  openDialog(elements.taskPowerDialog);
  elements.taskPowerCancelButton.focus();
}

function setTaskPowerBusy(busy) {
  state.taskPowerSaving = busy;
  for (const button of document.querySelectorAll('[data-task-power="1"]')) {
    button.disabled = busy;
  }
  elements.taskPowerConfirmButton.disabled = busy;
  elements.taskPowerCancelButton.disabled = busy;
  elements.taskPowerCloseButton.disabled = busy;
}

async function confirmTaskPower() {
  const task = state.taskPowerTarget;
  if (!task || state.taskPowerSaving || state.taskPowerPending) return;
  if (taskPowerPreflight(task, state.taskPowerMode)) return;
  const requestId = ++state.taskPowerRequestId;
  const mode = state.taskPowerMode;
  setTaskPowerBusy(true);
  try {
    const result = await bridge.apiPost(
      `tasks/${encodeURIComponent(task.id)}/${mode}`,
      { expected_revision: task.revision },
    );
    if (requestId !== state.taskPowerRequestId || state.taskPowerTarget?.id !== task.id) {
      return;
    }
    closeDialog(elements.taskPowerDialog);
    state.taskPowerTarget = null;
    const reloaded = await loadTasks();
    if (reloaded) await loadScheduleRuns(true);
    showStatus(
      reloaded
        ? `任务已${mode === "enable" ? "启用" : "停用"}；状态以最新读取为准。`
        : "启停请求已返回，但任务列表刷新失败，请重试读取。",
      reloaded ? "success" : "error",
    );
  } catch (error) {
    if (requestId !== state.taskPowerRequestId || state.taskPowerTarget?.id !== task.id) {
      return;
    }
    const message = friendlyErrorMessage(error);
    if (isConflictMessage(message) || isMissingMessage(message)) {
      closeDialog(elements.taskPowerDialog);
      state.taskPowerTarget = null;
      const reloaded = await loadTasks();
      showStatus(reloaded
        ? "任务已被修改或删除，已重新加载最新状态。"
        : "任务已被修改或删除，但刷新失败。", "error");
    } else if (isExplicitWriteRejection(message) || isInactiveMessage(message)) {
      elements.taskPowerError.textContent = message;
      elements.taskPowerError.hidden = false;
    } else {
      state.taskPowerPending = { taskId: task.id, phase: "unconfirmed" };
      elements.taskPowerUncertain.hidden = false;
      elements.taskPowerUncertainText.textContent =
        "服务器可能已经完成，也可能没有收到请求。为避免重复写入，请先重新读取最新任务状态。";
      elements.taskPowerRecheckButton.hidden = false;
      elements.taskPowerRecheckButton.disabled = false;
      elements.taskPowerReviewCloseButton.hidden = true;
    }
  } finally {
    if (requestId === state.taskPowerRequestId) {
      if (!state.taskPowerPending) setTaskPowerBusy(false);
    }
  }
}

async function recheckTaskPower() {
  const pending = state.taskPowerPending;
  if (!pending || pending.phase === "checking") return;
  pending.phase = "checking";
  elements.taskPowerRecheckButton.disabled = true;
  const succeeded = await loadTasks();
  if (state.taskPowerPending !== pending) return;
  if (!succeeded) {
    pending.phase = "unconfirmed";
    elements.taskPowerRecheckButton.disabled = false;
    elements.taskPowerUncertainText.textContent =
      "仍无法确认：重新读取任务失败。请不要重复提交。";
    return;
  }
  pending.phase = "review";
  const current = state.tasks.find((item) => item.id === pending.taskId);
  elements.taskPowerRecheckButton.hidden = true;
  elements.taskPowerReviewCloseButton.hidden = false;
  elements.taskPowerUncertainText.textContent = current
    ? `已重新读取。任务当前${current.enabled ? "已启用" : "未启用"}，revision ${current.revision}，实际下次时间 ${current.next_run_at || "无"}。`
    : "已重新读取，任务当前不在列表中。";
}

function closeTaskPowerAfterReview() {
  if (!state.taskPowerPending || state.taskPowerPending.phase !== "review") return;
  closeDialog(elements.taskPowerDialog);
  state.taskPowerPending = null;
  state.taskPowerTarget = null;
  setTaskPowerBusy(false);
  showStatus("已重新读取任务当前状态；未自动重试启停。", "success");
}

function openTaskDelete(task) {
  if (state.taskSaving || state.taskDeleting || state.taskPending || state.taskPowerPending) {
    return;
  }
  if (blockIfPendingWrite()) {
    return;
  }
  clearTaskPendingPanels();
  state.taskDeleteTarget = task;
  elements.taskDeleteMessage.textContent =
    `确定删除“${task.name}”吗？删除任务不会删除其内容组合或站点。`;
  elements.taskDeleteError.textContent = "";
  elements.taskDeleteError.hidden = true;
  elements.taskDeleteCancelButton.disabled = false;
  elements.taskDeleteConfirmButton.disabled = false;
  elements.taskDeleteConfirmButton.textContent = "删除";
  openDialog(elements.taskDeleteDialog);
  elements.taskDeleteCancelButton.focus();
}

async function confirmTaskDelete() {
  if (state.taskDeleting || state.taskPending || state.pendingWrite ||
      !state.taskDeleteTarget) {
    return;
  }
  const target = state.taskDeleteTarget;
  state.taskDeleting = true;
  elements.taskDeleteCancelButton.disabled = true;
  elements.taskDeleteConfirmButton.disabled = true;
  elements.taskDeleteConfirmButton.textContent = "删除中…";
  try {
    await bridge.apiPost(
      `tasks/${encodeURIComponent(target.id)}/delete`,
      { expected_revision: target.revision },
    );
    closeDialog(elements.taskDeleteDialog);
    state.taskDeleteTarget = null;
    const reloaded = await loadTasks();
    showStatus(
      reloaded ? `已删除“${target.name}”。` : "任务已删除，但列表刷新失败。",
      reloaded ? "success" : "error",
    );
  } catch (error) {
    const message = friendlyErrorMessage(error);
    if (isConflictMessage(message) || isMissingMessage(message)) {
      closeDialog(elements.taskDeleteDialog);
      state.taskDeleteTarget = null;
      const reloaded = await loadTasks();
      showStatus(
        reloaded
          ? "该任务已被修改或删除，已重新加载最新数据。"
          : "该任务已被修改或删除，但列表刷新失败。",
        "error",
      );
    } else if (
      message.includes("仍被推送任务引用") ||
      isExplicitWriteRejection(message)
    ) {
      elements.taskDeleteError.textContent = message;
      elements.taskDeleteError.hidden = false;
    } else {
      enterTaskPending("task_delete", writeFailureDetail(error), target.id);
    }
  } finally {
    state.taskDeleting = false;
    if (!state.taskPending) {
      elements.taskDeleteCancelButton.disabled = false;
      elements.taskDeleteConfirmButton.disabled = false;
      elements.taskDeleteConfirmButton.textContent = "删除";
    }
  }
}

function taskPendingElements(pending) {
  if (pending.kind === "task_delete") {
    return {
      panel: elements.taskDeleteUncertain,
      text: elements.taskDeleteUncertainText,
      recheck: elements.taskDeleteRecheckButton,
      review: elements.taskDeleteReviewCloseButton,
    };
  }
  return {
    panel: elements.taskWriteUncertain,
    text: elements.taskWriteUncertainText,
    recheck: elements.taskRecheckButton,
    review: elements.taskReviewCloseButton,
  };
}

function updateTaskPendingUI(text) {
  const pending = state.taskPending;
  if (!pending) {
    return;
  }
  const ui = taskPendingElements(pending);
  ui.panel.hidden = false;
  ui.text.textContent = text;
  if (pending.kind === "task_delete") {
    elements.taskDeleteCancelButton.disabled = true;
    elements.taskDeleteConfirmButton.disabled = true;
  } else {
    setTaskFormDisabled(true);
    elements.taskSaveButton.disabled = true;
    elements.taskSaveButton.textContent = "等待确认结果";
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
    ui.recheck.textContent = "重新读取最新任务";
    ui.review.hidden = true;
  }
}

function enterTaskPending(kind, detail, targetId) {
  state.taskPending = {
    kind,
    phase: "unconfirmed",
    targetId: targetId || null,
  };
  if (kind === "task_delete") {
    elements.taskDeleteError.hidden = true;
    elements.taskDeleteError.textContent = "";
  } else {
    clearTaskFormError();
  }
  updateTaskPendingUI(
    "服务器可能已经完成，也可能没有收到请求。" +
      "为避免重复写入，请先重新读取最新任务列表确认。" +
      `\n\n错误信息：${detail}`,
  );
}

function taskPendingReviewText(pending) {
  if (pending.kind === "create") {
    return (
      `已重新读取最新任务列表，当前共有 ${state.tasks.length} 个任务。` +
      "系统不能仅凭名称判断哪一条（如有）来自本次操作；" +
      "请关闭对话框查看列表并人工确认。"
    );
  }
  const task = pending.targetId
    ? state.tasks.find((item) => item.id === pending.targetId)
    : null;
  const current = task
    ? `仍在列表中：${task.name}（revision ${task.revision}）。`
    : "不在列表中。";
  return (
    `已重新读取最新任务列表。该任务当前${current}` +
    "请关闭对话框查看列表并判断是否符合预期；如需继续，请重新打开对应操作。"
  );
}

async function recheckTaskPending() {
  const pending = state.taskPending;
  if (!pending || pending.phase === "checking") {
    return;
  }
  pending.phase = "checking";
  updateTaskPendingUI("正在重新读取最新任务…");
  const succeeded = await loadTasks();
  if (state.taskPending !== pending) {
    return;
  }
  if (succeeded) {
    pending.phase = "review";
    updateTaskPendingUI(taskPendingReviewText(pending));
  } else {
    pending.phase = "unconfirmed";
    updateTaskPendingUI(
      "仍无法确认结果：重新读取最新任务失败。请不要重复提交；" +
        "保持此页面，稍后再次读取。",
    );
  }
}

function clearTaskPendingPanels() {
  elements.taskWriteUncertain.hidden = true;
  elements.taskWriteUncertainText.textContent = "";
  elements.taskReviewCloseButton.hidden = true;
  elements.taskDeleteUncertain.hidden = true;
  elements.taskDeleteUncertainText.textContent = "";
  elements.taskDeleteReviewCloseButton.hidden = true;
}

function finishTaskPendingReview() {
  const pending = state.taskPending;
  if (!pending || pending.phase !== "review") {
    return;
  }
  const deleting = pending.kind === "task_delete";
  closeDialog(deleting ? elements.taskDeleteDialog : elements.taskDialog);
  state.taskPending = null;
  clearTaskPendingPanels();
  if (deleting) {
    state.taskDeleteTarget = null;
  } else {
    setTaskFormDisabled(false);
    elements.taskSaveButton.textContent =
      state.taskMode === "create" ? "保存任务" : "保存更改";
  }
  showStatus(
    "已重新读取最新任务列表，并关闭未确认的操作。请根据列表判断结果。",
    "info",
  );
}


const DELIVERY_PAGE_SIZE = 20;
const DELIVERY_PENDING_KEY = "60sapi.pendingDelivery";

function isDeliveryFinal(status) {
  return ["success", "partial", "failed", "no_targets", "stopped"].includes(status);
}

function deliveryStatusLabel(status) {
  return ({
    preparing: "准备中", running: "运行中", success: "成功",
    partial: "部分成功", failed: "失败", unknown: "结果未知",
    no_targets: "空名单", stopped: "已停止", skipped: "未送达",
  })[status] || status;
}

const DELIVERY_ERROR_LABELS = {
  section_image_failed: "栏目图片生成失败",
};

function deliveryErrorLabel(code) {
  if (!code) {
    return "";
  }
  return DELIVERY_ERROR_LABELS[code] || code;
}

function deliveryFailedSectionCount(execution) {
  if (!execution) {
    return 0;
  }
  if (Number.isInteger(execution.failed_section_count)) {
    return execution.failed_section_count;
  }
  return Array.isArray(execution.failed_sections)
    ? execution.failed_sections.length
    : 0;
}

const DELIVERY_IMAGE_UNKNOWN_TEXT = "图片数量未知（记录不完整）";

function deliveryImageModel(execution) {
  return execution && execution.image_model === "sections" ? "sections" : "unknown";
}

// 记录数：目标 × 图片的行数，中性表述；旧字段 image_count 保持兼容。
function deliveryRecordCount(execution) {
  if (!execution) {
    return 0;
  }
  if (Number.isInteger(execution.image_record_count)) {
    return execution.image_record_count;
  }
  if (Number.isInteger(execution.image_count)) {
    return execution.image_count;
  }
  return 0;
}

function deliverySkippedRecordCount(execution) {
  if (!execution || !Number.isInteger(execution.skipped_image_count)) {
    return 0;
  }
  return execution.skipped_image_count;
}

// 只有 sections 才有栏目（槽位）数与实际生成数；旧记录一律 null，绝不推断。
function deliverySectionSlotCount(execution) {
  if (!execution || deliveryImageModel(execution) !== "sections") {
    return null;
  }
  return Number.isInteger(execution.section_slot_count)
    ? execution.section_slot_count
    : null;
}

// 生成数只在「记录完整且所有槽位 image_generated 都确定」时给数字；
// 槽位的发送状态（never_started/skipped 等）不能反推图片是否存在。
function deliveryGeneratedImageCount(execution) {
  if (!execution || deliveryImageModel(execution) !== "sections") {
    return null;
  }
  if (execution.record_complete === false) {
    return null;
  }
  return Number.isInteger(execution.generated_image_count)
    ? execution.generated_image_count
    : null;
}

function deliveryImageCountUnknown(execution) {
  if (!execution || deliveryImageModel(execution) !== "sections") {
    return false;
  }
  return execution.record_complete === false ||
    deliveryGeneratedImageCount(execution) === null;
}

function deliverySkippedReasons(execution) {
  if (!execution) {
    return [];
  }
  return [
    [execution.skipped_section_image_failed_count, "因栏目图片生成失败"],
    [execution.skipped_config_changed_count, "因配置变化"],
    [execution.skipped_other_count, "因其他或未知原因"],
  ]
    .filter(([count]) => Number.isInteger(count) && count > 0)
    .map(([count, label]) => count + " 条" + label);
}

// 「栏目没生成图」与「发送记录被跳过」是两件事；原因缺失时只说总数，不臆造原因。
function deliveryPartialSummary(execution) {
  const unknown = deliveryImageCountUnknown(execution);
  const sections = deliveryImageModel(execution) === "sections" && !unknown
    ? deliveryFailedSectionCount(execution)
    : 0;
  const records = deliverySkippedRecordCount(execution);
  const reasons = deliverySkippedReasons(execution);
  const parts = [];
  if (unknown) {
    parts.push(DELIVERY_IMAGE_UNKNOWN_TEXT);
  }
  if (sections > 0) {
    parts.push(sections + " 个栏目未生成图片");
  }
  if (records > 0) {
    parts.push(reasons.length > 0
      ? records + " 条发送记录未送达（" + reasons.join("，") + "）"
      : records + " 条发送记录未送达");
  }
  return parts.join("；");
}

function deliveryExecutionStatusLabel(execution) {
  const base = deliveryStatusLabel(execution && execution.status);
  if (execution && execution.status === "partial" && execution.image_partial) {
    const summary = deliveryPartialSummary(execution);
    if (summary) {
      return base + "（" + summary + "）";
    }
  }
  return base;
}

// 投递记录的一个槽位 = 一个栏目长图（图片输出）或一条逻辑消息（文字/Markdown）。
function deliverySlotLabel(execution, index) {
  const unit = execution && execution.output_format === "image" ? "张" : "条";
  return "第 " + index + " " + unit;
}

// 分档：sections 才提栏目/槽位；unknown（旧分页记录）只给中性的记录数。
function deliverySlotCountLabel(execution) {
  const records = deliveryRecordCount(execution);
  if (!execution || execution.output_format !== "image") {
    return records + " 条消息";
  }
  if (deliveryImageModel(execution) !== "sections") {
    return records + " 条图片记录";
  }
  const slots = deliverySectionSlotCount(execution);
  const generated = deliveryGeneratedImageCount(execution);
  if (deliveryImageCountUnknown(execution) || slots === null || generated === null) {
    const unknownParts = [DELIVERY_IMAGE_UNKNOWN_TEXT];
    if (records > 0) {
      unknownParts.push(records + " 条图片记录");
    }
    return unknownParts.join(" · ");
  }
  const parts = [];
  if (generated < slots) {
    parts.push(slots + " 个栏目（槽位）");
    parts.push("实际生成 " + generated + " 张图");
  } else {
    parts.push(slots + " 张栏目图");
  }
  if (records > 0) {
    parts.push(records + " 条图片记录");
  }
  return parts.join(" · ");
}

function readStoredDeliveryPending() {
  try {
    const raw = localStorage.getItem(DELIVERY_PENDING_KEY);
    const value = raw ? JSON.parse(raw) : null;
    if (value && typeof value.executionId === "string" &&
        typeof value.taskId === "string") {
      return { executionId: value.executionId, taskId: value.taskId };
    }
  } catch {
    state.deliveryStorageAvailable = false;
  }
  return null;
}

function readStoredDeliveryPendingMap() {
  try {
    const raw = localStorage.getItem(DELIVERY_PENDING_KEY);
    const value = raw ? JSON.parse(raw) : null;
    if (value && typeof value.executionId === "string" &&
        typeof value.taskId === "string") {
      return { [value.taskId]: { executionId: value.executionId, taskId: value.taskId } };
    }
    if (value && typeof value === "object") {
      const result = {};
      for (const [taskId, item] of Object.entries(value)) {
        if (item && typeof item.executionId === "string" &&
            typeof item.taskId === "string" && item.taskId === taskId) {
          result[taskId] = { executionId: item.executionId, taskId };
        }
      }
      return result;
    }
  } catch {
    state.deliveryStorageAvailable = false;
  }
  return {};
}

function currentDeliveryPendingMap() {
  if (!state.deliveryPendingMemory) {
    state.deliveryPendingMemory = readStoredDeliveryPendingMap();
  }
  return state.deliveryPendingMemory;
}

function deliveryPendingForTask(taskId) {
  return currentDeliveryPendingMap()[taskId] || null;
}

function setDeliveryPending(value) {
  const map = { ...currentDeliveryPendingMap() };
  if (value) {
    map[value.taskId] = {
      executionId: value.executionId, taskId: value.taskId,
    };
  } else {
    return;
  }
  state.deliveryPendingMemory = map;
  try {
    localStorage.setItem(DELIVERY_PENDING_KEY, JSON.stringify(map));
    state.deliveryStorageAvailable = true;
  } catch {
    // SecurityError/sandboxed iframe: keep the in-memory map. The current
    // session can still query; after refresh only server history remains.
    state.deliveryStorageAvailable = false;
  }
  state.deliveryPendingHiddenFor = null;
  syncDeliveryPendingPanel();
}

function confirmDeliveryPending(executionId) {
  const map = { ...currentDeliveryPendingMap() };
  const entry = Object.values(map).find(
    (item) => item.executionId === executionId,
  );
  if (!entry) {
    return;
  }
  delete map[entry.taskId];
  state.deliveryPendingMemory = map;
  try {
    localStorage.setItem(DELIVERY_PENDING_KEY, JSON.stringify(map));
    state.deliveryStorageAvailable = true;
  } catch {
    state.deliveryStorageAvailable = false;
  }
  if (state.deliveryPendingHiddenFor === executionId) {
    state.deliveryPendingHiddenFor = null;
  }
  syncDeliveryPendingPanel();
}

function syncDeliveryPendingPanel() {
  const entries = Object.values(currentDeliveryPendingMap());
  elements.deliveryPendingList.replaceChildren();
  elements.deliveryShowPendingButton.hidden = entries.length === 0;
  const hidden = entries.length === 0 ||
    state.deliveryPendingHiddenFor === "__all__";
  elements.deliveryPendingPanel.hidden = hidden;
  elements.deliveryPendingText.textContent = entries.length === 0
    ? ""
    : `${entries.length} 个执行结果尚未确认。系统不会自动重发；请按执行标识查询。` +
      (state.deliveryStorageAvailable
        ? " 刷新页面后仍可尝试恢复这些标识。"
        : " 当前 iframe 禁止本地存储：标识仅在当前页面内存中；刷新后需从执行记录人工核对，不能据此自动重发。");
  for (const pending of entries) {
    const row = document.createElement("div");
    row.className = "delivery-target";
    const code = document.createElement("code");
    code.textContent = pending.executionId;
    const action = makeButton("查询", "button button-secondary",
      () => queryPendingDelivery(pending.executionId),
      `查询未确认执行 ${pending.executionId}`);
    row.append(code, action);
    elements.deliveryPendingList.append(row);
  }
}

function queryPendingDelivery(executionId = null) {
  const entries = Object.values(currentDeliveryPendingMap());
  const target = executionId
    ? entries.find((item) => item.executionId === executionId)
    : entries[0];
  if (!target) {
    syncDeliveryPendingPanel();
    return;
  }
  state.deliveryMode = "pending";
  state.deliveryAttempt = {
    executionId: target.executionId, taskId: target.taskId, status: "unknown",
  };
  state.deliveryHistoryExecutionId = null;
  elements.deliveryDialogTitle.textContent = "查询未确认执行";
  elements.deliveryConfirmButton.hidden = true;
  elements.deliveryCancelButton.textContent = "关闭";
  elements.deliveryError.hidden = true;
  elements.deliveryResult.hidden = true;
  elements.deliveryUncertain.hidden = true;
  openDeliveryDialog();
  return queryDelivery(target.executionId);
}
function deliveryTargetText(target) {
  if (!target || target.valid === false) {
    return `不可投递${target && target.reason ? `（${target.reason}）` : ""}`;
  }
  const kind = target.message_type === "GroupMessage" ? "群聊" : "私聊";
  const isolated = target.isolated ? " · 成员隔离实际路由到群" : "";
  return `${kind} · 实际目标 ${target.target}${isolated}`;
}

function renderDeliveryPreview(data, token) {
  if (token !== state.deliveryPreviewRequestId ||
      state.deliveryMode !== "confirm") {
    return;
  }
  elements.deliveryConfirmInfo.replaceChildren();
  for (const [label, value] of [
    ["任务", data.task_name], ["组合", data.profile_name],
    ["输出方式", data.output_label],
    ["配置版本", `任务 ${data.task_revision} · 组合 ${data.profile_revision}`],
    ["内容来源", "按候选顺序选择第一个同时满足全部栏目与条数的站点；不会混用多个站点"],
    ["失败处理", "若没有任何候选站点能满足配置（含上游失败或条数不足），将改为向下列白名单发送一条文字异常提示"],
    ["内容策略", "重新准备内容，不复用页面旧预览；可能命中内容缓存（默认 300 秒），不保证重新请求上游"],
  ]) {
    const p = document.createElement("p");
    p.textContent = `${label}：${value}`;
    elements.deliveryConfirmInfo.append(p);
  }
  const candidates = Array.isArray(data.candidate_sites) ? data.candidate_sites : [];
  if (candidates.length > 0) {
    const order = document.createElement("p");
    order.textContent = `候选站点（按尝试顺序）：${candidates
      .map((item, index) => `${index + 1}. ${item.name || item.id}${item.enabled ? "" : "（已停用）"}`)
      .join(" → ")}`;
    elements.deliveryConfirmInfo.append(order);
  }
  if (data.alert_only) {
    const warning = document.createElement("p");
    warning.className = "form-error";
    warning.textContent =
      "本次不会发送内容：所有候选站点当前都不可用，将改为向下面的白名单发送一条文字异常提示。";
    elements.deliveryConfirmInfo.append(warning);
  }
  elements.deliveryTargets.replaceChildren();
  for (const target of data.targets || []) {
    const row = document.createElement("div");
    row.className = "delivery-target";
    const umo = document.createElement("code");
    umo.textContent = target.umo;
    const detail = document.createElement("span");
    detail.textContent = deliveryTargetText(target);
    row.append(umo, detail);
    elements.deliveryTargets.append(row);
  }
  if (!(data.targets || []).length) {
    const p = document.createElement("p");
    p.className = "form-error";
    p.textContent = "空白名单：不会获取内容，也不会发送。";
    elements.deliveryTargets.append(p);
  }
  elements.deliveryConfirmButton.hidden = false;
  elements.deliveryConfirmButton.disabled = !data.can_deliver;
  elements.deliveryConfirmButton.textContent = !data.can_deliver
    ? "不可投递"
    : (data.alert_only ? "确认并发送异常提示" : "确认并投递");
}

function openDeliveryDialog() {
  state.deliveryDialogGeneration += 1;
  elements.deliveryDialog.dataset.closeGeneration = "";
  openDialog(elements.deliveryDialog);
}

function invalidateDeliveryDialog() {
  state.deliveryMode = "closed";
  state.deliveryPreviewRequestId += 1;
  state.deliveryQueryRequestId += 1;
  state.deliveryHistoryExecutionId = null;
  state.deliveryQueryTarget = null;
  state.deliveryQueryHistory = false;
  state.deliveryPreview = null;
  state.deliveryAttempt = null;
}

function requestCloseDeliveryDialog() {
  if (state.deliveryBusy) {
    return;
  }
  elements.deliveryDialog.dataset.closeGeneration =
    String(state.deliveryDialogGeneration);
  invalidateDeliveryDialog();
  closeDialog(elements.deliveryDialog);
}

function resetDeliveryDialog(task, mode) {
  // A new dialog/mode must never keep an older detail-query target.
  state.deliveryQueryTarget = null;
  state.deliveryQueryHistory = false;
  state.deliveryQueryRequestId += 1;
  state.deliveryMode = mode;
  state.deliveryTask = task || null;
  state.deliveryPreview = null;
  state.deliveryAttempt = null;
  state.deliveryHistoryExecutionId = null;
  elements.deliveryDialogTitle.textContent = mode === "history"
    ? "执行详情" : "确认手动测试投递";
  elements.deliveryError.hidden = true;
  elements.deliveryResult.hidden = true;
  elements.deliveryUncertain.hidden = true;
  elements.deliveryConfirmInfo.replaceChildren();
  elements.deliveryTargets.replaceChildren();
  elements.deliveryConfirmButton.hidden = false;
  elements.deliveryConfirmButton.disabled = true;
  elements.deliveryConfirmButton.textContent = mode === "history" ? "只读详情" : "读取中…";
  elements.deliveryCancelButton.textContent = mode === "history" ? "关闭" : "取消";
}

async function openDelivery(task) {
  if (state.deliveryBusy || blockIfPendingWrite()) {
    return;
  }
  const pending = deliveryPendingForTask(task.id);
  if (pending) {
    await queryPendingDelivery(pending.executionId);
    return;
  }
  const token = ++state.deliveryPreviewRequestId;
  resetDeliveryDialog(task, "confirm");
  state.deliveryBusy = true;
  elements.deliveryConfirmInfo.textContent = "正在读取最新任务配置…";
  openDeliveryDialog();
  try {
    const result = await bridge.apiPost(
      `tasks/${encodeURIComponent(task.id)}/test-preview`, {},
    );
    if (token !== state.deliveryPreviewRequestId ||
        state.deliveryMode !== "confirm" ||
        state.deliveryTask !== task) {
      return;
    }
    state.deliveryPreview = result;
    renderDeliveryPreview(result, token);
    syncDeliveryPendingPanel();
  } catch (error) {
    if (token !== state.deliveryPreviewRequestId ||
        state.deliveryMode !== "confirm") {
      return;
    }
    elements.deliveryError.textContent = `无法读取确认信息：${friendlyErrorMessage(error)}`;
    elements.deliveryError.hidden = false;
    elements.deliveryConfirmInfo.textContent = "";
    elements.deliveryConfirmButton.disabled = true;
    elements.deliveryConfirmButton.textContent = "不可投递";
  } finally {
    if (token === state.deliveryPreviewRequestId) {
      state.deliveryBusy = false;
    }
  }
}

function newDeliveryExecutionId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `manual-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function renderDeliveryExecution(execution) {
  elements.deliveryResult.hidden = false;
  elements.deliveryResult.replaceChildren();
  const title = document.createElement("h3");
  title.textContent = `执行结果：${deliveryExecutionStatusLabel(execution)}`;
  const id = document.createElement("p");
  id.textContent = `执行标识：${execution.id}`;
  const content = document.createElement("p");
  content.textContent = `内容状态：${execution.content_status || "未知"} · ` +
    (execution.error_code ? `错误码：${execution.error_code}` : "无执行级错误");
  elements.deliveryResult.append(title, id, content);
  if (execution.status === "unknown") {
    const note = document.createElement("p");
    note.className = "task-disabled";
    note.textContent = "结果不确定，不会自动重发；请人工确认后再决定是否新建测试。";
    elements.deliveryResult.append(note);
  }
  for (const target of execution.targets || []) {
    const row = document.createElement("div");
    row.className = "delivery-target";
    const umo = document.createElement("code");
    umo.textContent = target.umo;
    const detail = document.createElement("span");
    const images = (target.images || []).map((page) => {
      const label = typeof page.label === "string" && page.label
        ? `（${page.label}）`
        : "";
      const code = deliveryErrorLabel(page.error_code);
      return `${deliverySlotLabel(execution, page.image_index)} ` +
        `${deliveryStatusLabel(page.status)}${label}` +
        (code ? `（${code}）` : "");
    }).join("；");
    detail.textContent = `${deliveryStatusLabel(target.status)} · ${images}`;
    row.append(umo, detail);
    elements.deliveryResult.append(row);
  }
}

async function submitDelivery(event) {
  event.preventDefault();
  if (state.deliveryBusy || state.deliveryMode !== "confirm" ||
      !state.deliveryPreview || !state.deliveryPreview.can_deliver ||
      state.deliveryAttempt) {
    return;
  }
  const preview = state.deliveryPreview;
  const task = state.deliveryTask;
  const executionId = newDeliveryExecutionId();
  const requestId = ++state.deliveryRequestId;
  state.deliveryBusy = true;
  state.deliveryAttempt = { executionId, taskId: task.id, status: "submitted" };
  // Bind the query context before the POST: a lost response must still query B.
  state.deliveryQueryTarget = executionId;
  state.deliveryQueryHistory = false;
  elements.deliveryConfirmButton.disabled = true;
  elements.deliveryConfirmButton.textContent = "已提交，等待结果…";
  elements.deliveryError.hidden = true;
  elements.deliveryUncertain.hidden = true;
  setDeliveryPending({ executionId, taskId: task.id });
  try {
    const execution = await bridge.apiPost(
      `tasks/${encodeURIComponent(task.id)}/test`,
      {
        execution_id: executionId,
        expected_task_revision: preview.task_revision,
        expected_profile_revision: preview.profile_revision,
        // 锁定确认时看到的候选顺序与 alert_only 语义：提交前状态变化
        // 必须重新审阅，不能改发未经确认的内容。
        expected_candidates: preview.candidate_snapshot,
        expected_alert_only: preview.alert_only === true,
      },
    );
    if (requestId !== state.deliveryRequestId ||
        !state.deliveryAttempt ||
        state.deliveryAttempt.executionId !== executionId ||
        execution.id !== executionId) {
      return;
    }
    state.deliveryMode = "result";
    state.deliveryAttempt.status = execution.status;
    elements.deliveryConfirmButton.hidden = true;
    renderDeliveryExecution(execution);
    if (isDeliveryFinal(execution.status)) {
      confirmDeliveryPending(executionId);
    }
    await loadDeliveries(true);
  } catch (error) {
    if (requestId !== state.deliveryRequestId ||
        !state.deliveryAttempt ||
        state.deliveryAttempt.executionId !== executionId) {
      return;
    }
    const message = friendlyErrorMessage(error);
    const staleConfirm = typeof message === "string"
      && message.includes("重新审阅");
    if (staleConfirm) {
      // 服务端在发送前拒绝了本次确认：没有任何消息发出，
      // 也不能锁定到并未创建的执行记录。
      state.deliveryMode = "conflict";
      state.deliveryPreview = null;
      state.deliveryAttempt = null;
      // 明确未发送：清除待确认标记，避免页面锁定到不存在的执行。
      confirmDeliveryPending(executionId);
      elements.deliveryConfirmButton.hidden = false;
      elements.deliveryConfirmButton.disabled = true;
      elements.deliveryConfirmButton.textContent = "请重新审阅";
      elements.deliveryError.textContent =
        "候选站点或配置在确认后发生变化，本次未发送任何消息；请关闭后重新打开确认。";
      elements.deliveryError.hidden = false;
      syncDeliveryPendingPanel();
      return;
    }
    if (isExplicitWriteRejection(message)) {
      // Explicit rejection happened before sending. A revision conflict
      // invalidates the confirmation; a new review is required.
      confirmDeliveryPending(executionId);
      state.deliveryMode = "conflict";
      state.deliveryPreview = null;
      elements.deliveryConfirmButton.disabled = true;
      elements.deliveryConfirmButton.hidden = false;
      elements.deliveryConfirmButton.textContent = "请重新审阅";
      elements.deliveryError.textContent =
        message.includes("配置版本") || message.includes("确认")
          ? "配置版本已变化，当前确认已作废，请关闭后重新打开确认。"
          : message;
      elements.deliveryError.hidden = false;
    } else {
      state.deliveryMode = "uncertain";
      state.deliveryAttempt.status = "unknown";
      elements.deliveryConfirmButton.hidden = true;
      elements.deliveryUncertain.hidden = false;
      elements.deliveryExecutionId.textContent = executionId;
      elements.deliveryUncertainText.textContent =
        "请求可能已被服务器接收，也可能没有。系统不会自动重发；请只查询该执行标识。";
    }
    syncDeliveryPendingPanel();
  } finally {
    if (requestId === state.deliveryRequestId) {
      state.deliveryBusy = false;
      // Never re-enable the old confirmation after submit, timeout or loss.
      elements.deliveryConfirmButton.disabled = true;
    }
  }
}

async function queryDelivery(executionId, options = {}) {
  const history = Boolean(options.history);
  state.deliveryQueryTarget = executionId;
  state.deliveryQueryHistory = history;
  const requestId = ++state.deliveryQueryRequestId;
  if (history) {
    state.deliveryMode = "history";
    state.deliveryHistoryExecutionId = executionId;
    elements.deliveryConfirmButton.hidden = true;
    elements.deliveryResult.hidden = true;
    elements.deliveryUncertain.hidden = true;
  }
  try {
    const execution = await bridge.apiGet(
      `executions/${encodeURIComponent(executionId)}`,
    );
    if (requestId !== state.deliveryQueryRequestId ||
        (history && state.deliveryHistoryExecutionId !== executionId)) {
      return false;
    }
    renderDeliveryExecution(execution);
    elements.deliveryError.hidden = true;
    elements.deliveryUncertain.hidden = true;
    if (isDeliveryFinal(execution.status)) {
      confirmDeliveryPending(executionId);
    }
    syncDeliveryPendingPanel();
    await loadDeliveries(true);
    return true;
  } catch (error) {
    if (requestId !== state.deliveryQueryRequestId) {
      return false;
    }
    const message = friendlyErrorMessage(error);
    elements.deliveryUncertain.hidden = false;
    elements.deliveryExecutionId.textContent = executionId;
    elements.deliveryUncertainText.textContent =
      `暂时无法确认执行结果：${message}。查询不到记录不等于没有发送；不会自动重发。`;
    return false;
  }
}

async function retryDeliveryQuery() {
  const executionId = state.deliveryQueryTarget;
  if (!executionId) {
    return;
  }
  await queryDelivery(executionId, {
    history: state.deliveryQueryHistory,
  });
}

async function openDeliveryHistory(execution) {
  if (state.deliveryBusy || blockIfPendingWrite()) {
    return;
  }
  resetDeliveryDialog(null, "history");
  state.deliveryHistoryExecutionId = execution.id;
  openDeliveryDialog();
  await queryDelivery(execution.id, { history: true });
}

function renderDeliveries(executions, append = false) {
  if (!append) {
    elements.deliveryList.replaceChildren();
  }
  if (!executions.length && !append) {
    elements.deliveryListStateText.textContent = "还没有手动测试记录。";
    elements.deliveryListState.hidden = false;
    elements.deliveryLoadMoreButton.hidden = true;
    return;
  }
  elements.deliveryListState.hidden = true;
  for (const execution of executions) {
    const card = document.createElement("li");
    card.className = "site-card";
    const head = document.createElement("div");
    head.className = "site-card-head";
    const title = document.createElement("h3");
    title.textContent = execution.task_name;
    const status = document.createElement("span");
    status.className = "badge";
    status.textContent = deliveryStatusLabel(execution.status);
    head.append(title, status);
    const meta = document.createElement("div");
    meta.className = "site-meta";
    const id = document.createElement("span");
    id.textContent = `执行：${execution.id}`;
    const counts = document.createElement("span");
    let countsText = `目标 ${execution.target_count} · ` +
      deliverySlotCountLabel(execution);
    if (execution.image_partial) {
      const summary = deliveryPartialSummary(execution);
      if (summary) {
        countsText += ` · ${summary}`;
      }
    }
    counts.textContent = countsText;
    const time = document.createElement("span");
    time.textContent = execution.created_at;
    meta.append(id, counts, time);
    const actions = document.createElement("div");
    actions.className = "profile-card-actions";
    actions.append(makeButton("查看详情", "button button-secondary",
      () => openDeliveryHistory(execution), `查看执行 ${execution.id}`));
    card.append(head, meta, actions);
    elements.deliveryList.append(card);
  }
}

const SCHEDULE_CLAIM_LABELS = {
  claimed: "已认领：投递可能开始或中断，不代表成功",
  succeeded: "投递完成：不代表平台已读",
  failed: "投递失败",
  unknown: "结果不确定：不会自动重试",
  skipped: "已认领但跳过发送",
};
const SCHEDULE_SKIP_LABELS = {
  late: "超过迟到窗口，已跳过",
  capacity: "调度容量已满，已跳过",
  overlap: "同任务执行重叠，已跳过",
  disabled: "任务已停用",
  schedule_invalid: "计划无效",
  config_unavailable: "配置读取失败",
  site_disabled: "站点已停用",
  profile_invalid: "内容组合无效",
  umos_invalid: "白名单或发送目标无效",
};

function scheduleTaskName(taskId) {
  const task = state.tasks.find((item) => item.id === taskId);
  return task ? `${task.name}（${task.id}）` : taskId;
}

function scheduleRunMeta(label, value) {
  const p = document.createElement("p");
  p.className = "schedule-run-meta";
  p.textContent = `${label}：${value || "—"}`;
  return p;
}

function scheduleCard(run, kind) {
  const card = document.createElement("li");
  card.className = "site-card";
  const head = document.createElement("div");
  head.className = "site-card-head";
  const title = document.createElement("h4");
  title.textContent = scheduleTaskName(run.task_id);
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = kind === "claim"
    ? (SCHEDULE_CLAIM_LABELS[run.status] || run.status)
    : (SCHEDULE_SKIP_LABELS[run.reason] || run.reason);
  head.append(title, badge);
  card.append(head);
  if (kind === "claim") {
    card.append(
      scheduleRunMeta("UTC 计划时刻", run.scheduled_for),
      scheduleRunMeta("状态", run.status),
      scheduleRunMeta("执行 ID", run.execution_id),
      scheduleRunMeta("错误码", run.error_code),
      scheduleRunMeta("认领时间", run.created_at),
      scheduleRunMeta("更新时间", run.updated_at),
    );
  } else {
    card.append(
      scheduleRunMeta("UTC 计划时刻", run.scheduled_for),
      scheduleRunMeta("跳过原因", run.reason),
      scheduleRunMeta("记录时间", run.created_at),
    );
  }
  return card;
}

function renderScheduleRuns() {
  elements.scheduleClaimsList.replaceChildren(
    ...state.scheduleClaims.map((run) => scheduleCard(run, "claim")),
  );
  elements.scheduleSkipsList.replaceChildren(
    ...state.scheduleSkips.map((run) => scheduleCard(run, "skip")),
  );
  elements.scheduleClaimsEmpty.hidden = state.scheduleClaims.length > 0;
  elements.scheduleSkipsEmpty.hidden = state.scheduleSkips.length > 0;
  elements.scheduleRunsMoreButton.hidden = !state.scheduleRunsHasMore;
  elements.scheduleClaimsList.setAttribute("aria-busy", "false");
  elements.scheduleSkipsList.setAttribute("aria-busy", "false");
}

async function loadScheduleRuns(reset = true) {
  state.scheduleRunsRetryReset = reset;
  const requestId = ++state.scheduleRunsRequestId;
  const requestOffset = reset ? 0 : state.scheduleRunsOffset;
  elements.scheduleClaimsList.setAttribute("aria-busy", "true");
  elements.scheduleSkipsList.setAttribute("aria-busy", "true");
  elements.scheduleRunsStateText.textContent = "正在读取调度记录…";
  elements.scheduleRunsState.hidden = false;
  elements.scheduleRunsRetryButton.hidden = true;
  try {
    const result = await bridge.apiGet("schedule-runs", {
      limit: 10,
      offset: requestOffset,
    });
    if (requestId !== state.scheduleRunsRequestId) return false;
    const claims = Array.isArray(result.claims) ? result.claims : [];
    const skips = Array.isArray(result.skips) ? result.skips : [];
    // Only a successful current request replaces the committed display.
    state.scheduleClaims = reset
      ? claims : state.scheduleClaims.concat(claims);
    state.scheduleSkips = reset
      ? skips : state.scheduleSkips.concat(skips);
    const limit = Number(result.limit) || 10;
    state.scheduleRunsOffset = requestOffset + Math.max(claims.length, skips.length);
    state.scheduleRunsHasMore = claims.length >= limit || skips.length >= limit;
    renderScheduleRuns();
    elements.scheduleRunsState.hidden = true;
    return true;
  } catch (error) {
    if (requestId !== state.scheduleRunsRequestId) return false;
    // Keep the last successfully loaded arrays, offset and hasMore.
    elements.scheduleRunsStateText.textContent =
      `读取调度记录失败：${friendlyErrorMessage(error)}`;
    elements.scheduleRunsState.hidden = false;
    elements.scheduleRunsRetryButton.hidden = false;
    return false;
  } finally {
    if (requestId === state.scheduleRunsRequestId) {
      elements.scheduleClaimsList.setAttribute("aria-busy", "false");
      elements.scheduleSkipsList.setAttribute("aria-busy", "false");
    }
  }
}

async function loadDeliveries(reset = true) {
  if (reset) {
    state.deliveryHistory = [];
    state.deliveryOffset = 0;
    state.deliveryHasMore = false;
  }
  const requestId = ++state.deliveryLoadRequestId;
  elements.deliveryList.setAttribute("aria-busy", "true");
  try {
    const result = await bridge.apiGet("executions", {
      limit: DELIVERY_PAGE_SIZE,
      offset: state.deliveryOffset,
    });
    if (requestId !== state.deliveryLoadRequestId) {
      return;
    }
    const items = Array.isArray(result.executions) ? result.executions : [];
    state.deliveryHistory = reset
      ? items : state.deliveryHistory.concat(items);
    state.deliveryOffset += items.length;
    state.deliveryHasMore = Boolean(result.has_more);
    renderDeliveries(items, !reset);
    elements.deliveryLoadMoreButton.hidden = !state.deliveryHasMore;
  } catch (error) {
    if (requestId !== state.deliveryLoadRequestId) {
      return;
    }
    elements.deliveryListStateText.textContent =
      `读取执行记录失败：${friendlyErrorMessage(error)}`;
    elements.deliveryListState.hidden = false;
  } finally {
    if (requestId === state.deliveryLoadRequestId) {
      elements.deliveryList.setAttribute("aria-busy", "false");
    }
  }
}
async function refreshAll() {
  await Promise.all([loadSites(), loadProfiles(), loadTasks(), loadScheduleRuns(true), loadDeliveries(true)]);
}

function bindEvents() {
  elements.taskSiteAddButton.addEventListener("click", addTaskSite);
  elements.taskProfile.addEventListener("change", updateTaskCapabilityGate);
  elements.taskCapabilityTestButton.addEventListener(
    "click", runTaskCapabilityTest,
  );
  elements.siteTestAllButton.addEventListener("click", runSiteTestAll);
  // 管理员明确选择来源类型后，未知提示应立即消失，避免继续误导。
  elements.sourceKind.addEventListener("change", () => {
    setSourceKindHint(!isKnownSourceKind(elements.sourceKind.value));
  });
  for (const preset of PRESET_INSTANCES) {
    const option = document.createElement("option");
    option.value = preset.value;
    option.textContent = preset.label;
    elements.instancePreset.append(option);
  }
  elements.batchTestRun?.addEventListener("click", runBatchTest);
  elements.instanceTypePublic.addEventListener("change", () => {
    if (elements.instanceTypePublic.checked) selectInstanceType(SOURCE_KIND_PUBLIC);
  });
  elements.instanceTypeSelf.addEventListener("change", () => {
    if (elements.instanceTypeSelf.checked) selectInstanceType(SOURCE_KIND_SELF_BUILT);
  });
  for (const action of elements.siteForm.querySelectorAll(
    'input[name="credential_action"]',
  )) {
    action.addEventListener("change", () => {
      // 管理员明确选择后，不再视作系统自动清除。
      state.credentialAutoCleared = false;
    });
  }
  elements.baseUrl.addEventListener("change", handleBaseUrlChanged);
  elements.baseUrl.addEventListener("input", rememberCustomAddress);
  elements.instancePreset.addEventListener("change", () => {
    const next = elements.instancePreset.value;
    if (next === "custom") {
      // 离开预设：恢复用户此前填写的自定义地址。
      elements.baseUrl.value = state.customBaseUrl || "";
    } else {
      // 进入预设：先记住当前自定义地址再替换。
      rememberCustomAddress();
      elements.baseUrl.value = next;
    }
    applyAddressChange();
  });
  elements.createTaskButton.addEventListener("click", openCreateTask);
  elements.deliveryRefreshButton.addEventListener("click", () => loadDeliveries(true));
  elements.deliveryLoadMoreButton.addEventListener("click", () => loadDeliveries(false));
  elements.deliveryForm.addEventListener("submit", submitDelivery);
  elements.deliveryQueryButton.addEventListener("click", retryDeliveryQuery);
  elements.deliveryShowPendingButton.addEventListener("click", () => {
    state.deliveryPendingHiddenFor = null;
    syncDeliveryPendingPanel();
  });
  elements.deliveryClearPendingButton.addEventListener("click", () => {
    if (Object.keys(currentDeliveryPendingMap()).length) {
      // Hide only; the tracked executions and the permanent show entry remain.
      state.deliveryPendingHiddenFor = "__all__";
      syncDeliveryPendingPanel();
    }
  });
  elements.deliveryCancelButton.addEventListener("click", requestCloseDeliveryDialog);
  elements.deliveryCloseButton.addEventListener("click", requestCloseDeliveryDialog);
  elements.deliveryDialog.addEventListener("cancel", (event) => {
    if (state.deliveryBusy) {
      event.preventDefault();
      return;
    }
    // Invalidate synchronously for Escape; the later native close event must
    // not clear a dialog that was reopened in the meantime.
    elements.deliveryDialog.dataset.closeGeneration =
      String(state.deliveryDialogGeneration);
    invalidateDeliveryDialog();
  });
  elements.deliveryDialog.addEventListener("close", () => {
    if (state.deliveryBusy) {
      return;
    }
    const closingGeneration = Number(
      elements.deliveryDialog.dataset.closeGeneration || "",
    );
    if (closingGeneration !== state.deliveryDialogGeneration) {
      // A late close from an older dialog generation.
      return;
    }
    invalidateDeliveryDialog();
  });
  elements.taskRetryButton.addEventListener("click", loadTasks);
  elements.taskForm.addEventListener("submit", handleTaskSave);
  elements.taskScheduleNone.addEventListener("change", () => {
    setTaskScheduleMode("none");
    markTaskScheduleDirty();
  });
  elements.taskScheduleDaily.addEventListener("change", () => {
    setTaskScheduleMode("daily");
    markTaskScheduleDirty();
  });
  elements.taskScheduleCron.addEventListener("change", () => {
    setTaskScheduleMode("cron");
    markTaskScheduleDirty();
  });
  elements.taskScheduleExpression.addEventListener("input", markTaskScheduleDirty);
  elements.taskScheduleInterval.addEventListener("change", () => {
    setTaskScheduleMode("interval");
    markTaskScheduleDirty();
  });
  elements.taskScheduleStartAt.addEventListener("input", markTaskScheduleDirty);
  elements.taskScheduleIntervalMinutes.addEventListener("input", markTaskScheduleDirty);
  elements.taskScheduleWeekly.addEventListener("change", () => {
    setTaskScheduleMode("weekly");
    markTaskScheduleDirty();
  });
  for (const input of elements.taskWeekdays.querySelectorAll("input")) {
    input.addEventListener("change", markTaskScheduleDirty);
  }
  elements.taskScheduleTime.addEventListener("input", markTaskScheduleDirty);
  elements.taskScheduleTimezone.addEventListener("input", markTaskScheduleDirty);
  elements.taskDeleteConfirmButton.addEventListener("click", confirmTaskDelete);
  elements.taskPowerConfirmButton.addEventListener("click", confirmTaskPower);
  elements.taskPowerRecheckButton.addEventListener("click", recheckTaskPower);
  elements.taskPowerReviewCloseButton.addEventListener(
    "click", closeTaskPowerAfterReview,
  );
  elements.taskPowerCancelButton.addEventListener("click", () => {
    if (!state.taskPowerSaving && !state.taskPowerPending) {
      closeDialog(elements.taskPowerDialog);
      state.taskPowerTarget = null;
    }
  });
  elements.taskPowerCloseButton.addEventListener("click", () => {
    if (!state.taskPowerSaving && !state.taskPowerPending) {
      closeDialog(elements.taskPowerDialog);
      state.taskPowerTarget = null;
    }
  });
  elements.taskPowerDialog.addEventListener("cancel", (event) => {
    if (state.taskPowerSaving || state.taskPowerPending) {
      event.preventDefault();
    }
  });
  elements.scheduleRunsRefreshButton.addEventListener(
    "click", () => loadScheduleRuns(true),
  );
  elements.scheduleRunsRetryButton.addEventListener(
    "click", () => loadScheduleRuns(state.scheduleRunsRetryReset),
  );
  elements.scheduleRunsMoreButton.addEventListener(
    "click", () => loadScheduleRuns(false),
  );
  elements.taskRecheckButton.addEventListener("click", recheckTaskPending);
  elements.taskReviewCloseButton.addEventListener("click", finishTaskPendingReview);
  elements.taskDeleteRecheckButton.addEventListener("click", recheckTaskPending);
  elements.taskDeleteReviewCloseButton.addEventListener(
    "click",
    finishTaskPendingReview,
  );
  elements.taskCancelButton.addEventListener("click", () => {
    if (!state.taskSaving && !state.taskPending) {
      requestCloseFormDialog(elements.taskDialog);
    }
  });
  elements.taskCloseDialogButton.addEventListener("click", () => {
    if (!state.taskSaving && !state.taskPending) {
      requestCloseFormDialog(elements.taskDialog);
    }
  });
  elements.taskDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    if (state.taskSaving || state.taskPending) {
      return;
    }
    requestCloseFormDialog(elements.taskDialog);
  });
  elements.taskDeleteCancelButton.addEventListener("click", () => {
    if (!state.taskDeleting && !state.taskPending) {
      closeDialog(elements.taskDeleteDialog);
    }
  });
  elements.taskDeleteDialog.addEventListener("cancel", (event) => {
    if (state.taskDeleting || state.taskPending) {
      event.preventDefault();
    }
  });
  elements.taskDeleteDialog.addEventListener("close", () => {
    if (!state.taskPending) {
      state.taskDeleteTarget = null;
    }
  });
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
  elements.profileImageButton.addEventListener("click", generateProfileImage);
  elements.profileDownloadButton.addEventListener(
    "click",
    downloadProfileOutput,
  );
  elements.profilePreviewSelect.addEventListener("change", () => {
    state.profilePreviewId = elements.profilePreviewSelect.value;
    resetProfilePreview();
    setProfilePreviewBusy(false);
  });
  elements.profilePreviewSite.addEventListener("change", () => {
    state.profilePreviewSiteId = elements.profilePreviewSite.value;
    // 结果绑定到具体站点：换站点后旧结果和长图都不能继续使用。
    resetProfilePreview();
    resetProfileImages();
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
      requestCloseFormDialog(elements.profileDialog);
    }
  });
  elements.profileCancelButton.addEventListener("click", () => {
    if (!state.profileSaving && !state.profilePending) {
      requestCloseFormDialog(elements.profileDialog);
    }
  });
  elements.profileDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    if (state.profileSaving || state.profilePending) {
      return;
    }
    requestCloseFormDialog(elements.profileDialog);
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
      requestCloseFormDialog(elements.siteDialog);
    }
  });
  elements.closeDialogButton.addEventListener("click", () => {
    if (!state.saving && !state.pendingWrite) {
      requestCloseFormDialog(elements.siteDialog);
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
    event.preventDefault();
    if (state.saving || state.pendingWrite) {
      return;
    }
    requestCloseFormDialog(elements.siteDialog);
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

/* ---------- 视图导航：只切换显示状态，不读取或写入任何业务数据 ---------- */

const NAV_VIEW_DEFINITIONS = [
  { view: "sites", label: "站点管理" },
  { view: "preview", label: "内容预览" },
  { view: "profiles", label: "内容组合" },
  { view: "tasks", label: "推送任务" },
  { view: "executions", label: "执行记录" },
];

const NAV_VIEW_IDS = new Set(
  NAV_VIEW_DEFINITIONS.map((item) => item.view),
);
const NAV_VIEW_LABELS = new Map(
  NAV_VIEW_DEFINITIONS.map((item) => [item.view, item.label]),
);
const NAV_DEFAULT_VIEW = "sites";

const navState = {
  current: null,
};

function navViewNodes() {
  return Array.from(document.querySelectorAll(".view[data-view]"));
}

function navItemNodes() {
  return Array.from(document.querySelectorAll(".nav-item[data-view]"));
}

function navViewNode(view) {
  for (const node of navViewNodes()) {
    if (node.getAttribute("data-view") === view) {
      return node;
    }
  }
  return null;
}

function navRawHash() {
  return String(window.location.hash || "").replace(/^#/, "").trim();
}

function navHashView() {
  const raw = navRawHash();
  return NAV_VIEW_IDS.has(raw) ? raw : null;
}

function navIsPageAnchor(raw) {
  return Boolean(raw) && document.getElementById(raw) !== null;
}

function navWriteHash(view) {
  const target = "#" + view;
  if (String(window.location.hash || "") === target) {
    return;
  }
  try {
    window.history.replaceState(null, "", target);
  } catch {
    window.location.hash = target;
  }
}

function navUpdateBreadcrumb(view) {
  const breadcrumb = document.getElementById("breadcrumb-view");
  if (!breadcrumb) {
    return;
  }
  const label = NAV_VIEW_LABELS.get(view);
  breadcrumb.textContent = label ? "工作区 / " + label : "工作区";
}

function navFocusView(view) {
  const node = navViewNode(view);
  if (!node) {
    return;
  }
  const heading = node.querySelector("h1") || node;
  if (!heading.hasAttribute("tabindex")) {
    heading.setAttribute("tabindex", "-1");
  }
  try {
    heading.focus();
  } catch {
    // 焦点移动失败不影响视图切换。
  }
}

function navApplyView(view, options) {
  if (!NAV_VIEW_IDS.has(view)) {
    return false;
  }
  const nodes = navViewNodes();
  if (nodes.length === 0) {
    return false;
  }
  const settings = options || {};
  for (const node of nodes) {
    node.hidden = node.getAttribute("data-view") !== view;
  }
  for (const item of navItemNodes()) {
    const active = item.getAttribute("data-view") === view;
    item.classList.toggle("is-active", active);
    if (active) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  }
  navState.current = view;
  // 站点专用操作只属于站点页，避免在其他页面出现“新增站点”。
  const headerActions = document.getElementById("header-actions");
  if (headerActions) {
    headerActions.hidden = view !== "sites";
  }
  navUpdateBreadcrumb(view);
  if (settings.updateHash) {
    navWriteHash(view);
  }
  if (settings.focus) {
    navFocusView(view);
  }
  return true;
}

function navRequestView(view, options) {
  if (!NAV_VIEW_IDS.has(view)) {
    return false;
  }
  return navApplyView(view, options || {});
}

function navHandleHashChange() {
  const raw = navRawHash();
  if (!raw) {
    return;
  }
  const view = navHashView();
  if (view) {
    navRequestView(view, { focus: true, updateHash: false });
    return;
  }
  if (navIsPageAnchor(raw)) {
    return;
  }
  navRequestView(NAV_DEFAULT_VIEW, { focus: true, updateHash: false });
}

function bindNavigation() {
  for (const item of navItemNodes()) {
    item.addEventListener("click", () => {
      const view = item.getAttribute("data-view");
      if (view) {
        navRequestView(view, { focus: true, updateHash: true });
      }
    });
  }
  window.addEventListener("hashchange", navHandleHashChange);
  navApplyView(navHashView() || NAV_DEFAULT_VIEW, {
    focus: false,
    updateHash: false,
  });
}

/* ---------- 未保存修改守卫：关闭表单对话框前比对初始快照 ---------- */

const formGuardState = {
  baselines: new WeakMap(),
  pendingDialog: null,
};

function formSnapshot(form) {
  const parts = [];
  const separator = String.fromCharCode(1);
  let index = 0;
  for (const control of form.elements) {
    index += 1;
    const tag = String(control.tagName || "").toUpperCase();
    if (!tag) {
      continue;
    }
    if (
      tag === "BUTTON"
      || tag === "FIELDSET"
      || tag === "OUTPUT"
      || tag === "OBJECT"
    ) {
      continue;
    }
    const type = String(control.type || "").toLowerCase();
    if (
      type === "submit"
      || type === "button"
      || type === "reset"
      || type === "file"
      || type === "image"
    ) {
      continue;
    }
    const key = control.id || control.name || tag + ":" + index;
    if (type === "checkbox" || type === "radio") {
      parts.push(key + "=" + (control.checked ? "1" : "0"));
    } else {
      parts.push(
        key + "=" + (control.value === undefined ? "" : control.value),
      );
    }
  }
  return parts.join(separator);
}

function captureFormBaselines(dialog) {
  if (!dialog || typeof dialog.querySelectorAll !== "function") {
    return;
  }
  for (const form of dialog.querySelectorAll("form")) {
    formGuardState.baselines.set(form, formSnapshot(form));
  }
}

function formHasUnsavedChanges(form) {
  const baseline = formGuardState.baselines.get(form);
  if (typeof baseline !== "string") {
    return false;
  }
  return formSnapshot(form) !== baseline;
}

function dialogHasUnsavedChanges(dialog) {
  if (!dialog) {
    return false;
  }
  for (const form of dialog.querySelectorAll("form")) {
    if (formHasUnsavedChanges(form)) {
      return true;
    }
  }
  return false;
}

function closeDiscardDialog() {
  const dialog = document.getElementById("discard-dialog");
  if (!dialog) {
    return;
  }
  if (typeof dialog.close === "function") {
    if (dialog.open) {
      dialog.close();
    }
  } else {
    dialog.removeAttribute("open");
  }
}

function resolveDiscard(leave) {
  const formDialog = formGuardState.pendingDialog;
  formGuardState.pendingDialog = null;
  closeDiscardDialog();
  if (leave && formDialog && formDialog.open) {
    closeDialog(formDialog);
  }
}

function ensureDiscardDialogBound(dialog) {
  if (!dialog || dialog.dataset.guardBound === "1") {
    return;
  }
  dialog.dataset.guardBound = "1";
  const confirmButton = document.getElementById("discard-confirm-button");
  const cancelButton = document.getElementById("discard-cancel-button");
  if (confirmButton) {
    confirmButton.addEventListener("click", () => resolveDiscard(true));
  }
  if (cancelButton) {
    cancelButton.addEventListener("click", () => resolveDiscard(false));
  }
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    resolveDiscard(false);
  });
  dialog.addEventListener("close", () => {
    formGuardState.pendingDialog = null;
  });
}

function openDiscardDialog(formDialog) {
  const dialog = document.getElementById("discard-dialog");
  if (!dialog) {
    // 页面模板尚未提供 #discard-dialog 时不静默丢弃修改。
    const leave = typeof window.confirm === "function"
      ? window.confirm("放弃未保存的修改？离开会丢失当前填写的内容。")
      : false;
    if (leave) {
      closeDialog(formDialog);
    }
    return;
  }
  ensureDiscardDialogBound(dialog);
  formGuardState.pendingDialog = formDialog;
  if (typeof dialog.showModal === "function") {
    if (!dialog.open) {
      dialog.showModal();
    }
  } else {
    dialog.setAttribute("open", "");
  }
  const cancelButton = document.getElementById("discard-cancel-button");
  if (cancelButton) {
    cancelButton.focus();
  }
}

function requestCloseFormDialog(dialog) {
  if (!dialog || !dialog.open) {
    return;
  }
  if (!dialogHasUnsavedChanges(dialog)) {
    closeDialog(dialog);
    return;
  }
  openDiscardDialog(dialog);
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
  await Promise.all([loadSites(), loadProfiles(), loadTasks(), loadScheduleRuns(true), loadDeliveries(true)]);
  syncDeliveryPendingPanel();
}

bindNavigation();
init();

document.getElementById("site-test-close-button")?.addEventListener("click", closeSiteTest);
document.getElementById("site-test-run-button")?.addEventListener("click", runSiteTest);

document.getElementById("site-test-content-type")?.addEventListener("change", () => {
  siteTestDisplayToken += 1;
  setSiteTestResult("", null);
  siteTestRunning(siteTestSite, document.getElementById("site-test-run-button"));
});
document.getElementById("site-test-dialog")?.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeSiteTest();
});
