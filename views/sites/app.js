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
  deleteTitle: document.getElementById("delete-title"),
  deleteSelection: document.getElementById("delete-selection"),
  deleteResults: document.getElementById("delete-results"),
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
  taskItemPicker: document.getElementById("task-item-picker"),
  taskItemAddButton: document.getElementById("task-item-add-button"),
  taskItemList: document.getElementById("task-item-list"),
  taskPreviewFormButton: document.getElementById("task-preview-form-button"),
  taskPreviewDialog: document.getElementById("task-preview-dialog"),
  taskPreviewTitle: document.getElementById("task-preview-title"),
  taskPreviewRoot: document.getElementById("task-preview-root"),
  taskPreviewStatus: document.getElementById("task-preview-status"),
  taskPreviewMeta: document.getElementById("task-preview-meta"),
  taskPreviewSections: document.getElementById("task-preview-sections"),
  taskPreviewFailed: document.getElementById("task-preview-failed"),
  taskPreviewOutputText: document.getElementById("task-preview-output-text"),
  taskPreviewOutputHint: document.getElementById("task-preview-output-hint"),
  taskPreviewCopyButton: document.getElementById("task-preview-copy-button"),
  taskPreviewDownloadButton: document.getElementById("task-preview-download-button"),
  taskPreviewImageButton: document.getElementById("task-preview-image-button"),
  taskPreviewImageState: document.getElementById("task-preview-image-state"),
  taskPreviewImageHint: document.getElementById("task-preview-image-hint"),
  taskPreviewImagePages: document.getElementById("task-preview-image-pages"),
  taskPreviewRefreshButton: document.getElementById("task-preview-refresh-button"),
  taskPreviewDoneButton: document.getElementById("task-preview-done-button"),
  taskPreviewCloseButton: document.getElementById("task-preview-close-button"),
  previewImageViewer: document.getElementById("preview-image-viewer"),
  previewImageViewerTitle: document.getElementById("preview-image-viewer-title"),
  previewImageViewerBody: document.getElementById("preview-image-viewer-body"),
  previewImageViewerImg: document.getElementById("preview-image-viewer-img"),
  previewImageViewerHint: document.getElementById("preview-image-viewer-hint"),
  previewImageViewerClose: document.getElementById("preview-image-viewer-close"),
  previewImageViewerDownload: document.getElementById("preview-image-viewer-download"),
  previewImageViewerZoom: document.getElementById("preview-image-viewer-zoom"),
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
  taskSitePicker: document.getElementById("task-site-picker"),
  taskSiteAddButton: document.getElementById("task-site-add-button"),
  taskSiteList: document.getElementById("task-site-list"),
  taskCapabilityTestButton: document.getElementById("task-capability-test-button"),
  taskCapabilityResult: document.getElementById("task-capability-result"),
  siteSelection: document.getElementById("site-selection"),
  siteSelectionCount: document.getElementById("site-selection-count"),
  siteSelectAll: document.getElementById("site-select-all"),
  siteSelectNone: document.getElementById("site-select-none"),
  siteSelectInvert: document.getElementById("site-select-invert"),
  siteTestSelected: document.getElementById("site-test-selected"),
  siteDeleteSelected: document.getElementById("site-delete-selected"),
  siteTestSummary: document.getElementById("site-test-summary"),
  taskOutputFormat: document.getElementById("task-output-format"),
  taskImageStyle: document.getElementById("task-image-style"),
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
  siteSelection: new Set(),
  siteTest: new Map(),
  siteTestBusy: false,
  sites: [],
  mode: "create",
  editingId: null,
  editingRevision: null,
  deleteTarget: null,
  deleteTargets: [],
  deleteMode: "single",
  loading: false,
  saving: false,
  deleting: false,
  pendingWrite: null,
  previewLoading: false,
  previewSiteId: null,
  previewRequestId: 0,
  taskItems: [],
  taskPreviewLoading: false,
  taskPreviewTaskId: null,
  taskPreviewTaskRevision: null,
  taskPreviewRequestId: 0,
  taskPreviewSnapshotId: null,
  taskPreviewOutputs: null,
  taskPreviewFormat: "text",
  taskPreviewOutputEpoch: 0,
  taskPreviewCopyOperationId: 0,
  taskPreviewImageSnapshotId: null,
  taskPreviewImageRequestId: 0,
  taskPreviewImageLoading: false,
  imageViewerSection: null,
  imageViewerTrigger: null,
  imageViewerScrollY: 0,
  imageViewerActual: false,
  customBaseUrl: "",
  credentialAutoCleared: false,
  tasks: [],
  taskMode: "create",
  imageStyles: [],
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
  // 任务表单的完整签名与请求序号：在途能力测试/预览只认当前这一代。
  taskFormEpoch: 0,
  taskFormHintText: "",
  taskCapabilityTestRequestId: 0,
  // 最新一次在途能力测试的序号（0=空闲）：忙状态不能只按表单推导。
  taskCapabilityTestInflightId: 0,
  // 表单错误区里是不是我们写的「条数非法」：条数改对后由这里收回。
  taskLimitErrorShown: false,
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

const SITE_TEST_TYPES = [
  ["daily_news", "每日新闻"],
  ["bilibili_hot", "Bilibili 热榜"],
  ["zhihu_hot", "知乎热榜"],
  ["douyin_hot", "抖音热搜"],
  ["toutiao_hot", "今日头条"],
];
// 后端单次最多接受 20 个站点：超过时按块顺序请求，避免整批失败。
const SITE_TEST_CHUNK = 20;
const SITE_TEST_STATE_TEXT = {
  idle: "未测试",
  running: "测试中",
  success: "成功",
  empty: "合法空内容",
  error: "失败",
  // 结果对应的站点版本与当前列表不同：既不当作成功，也不当作失败。
  stale: "需重新测试",
};
// 状态图标用同一套描边图形，不依赖字体里的符号：字号被放大或替换字体时也能对齐。
const SITE_TEST_STATE_ICON = {
  idle: [["circle", { cx: 12, cy: 12, r: 6.4 }]],
  running: [["circle", { cx: 12, cy: 12, r: 6.4, "stroke-dasharray": "9 31", "stroke-linecap": "round" }]],
  success: [["path", { d: "M5.4 12.4 9.8 16.8 18.6 7.2" }]],
  empty: [["circle", { cx: 12, cy: 12, r: 6.4 }], ["path", { d: "M8.9 12h6.2" }]],
  error: [["path", { d: "M7.6 7.6 16.4 16.4" }], ["path", { d: "M16.4 7.6 7.6 16.4" }]],
  stale: [["path", { d: "M18.4 12a6.4 6.4 0 1 1-1.9-4.5" }], ["path", { d: "M18.4 4.4v4.2h-4.2" }]],
};
const SVG_NS = "http://www.w3.org/2000/svg";

/** 用描边几何生成小图标：形状写成 [标签, 属性] 列表，便于按状态复用。 */
function createSvgIcon(shape, options) {
  const settings = options || {};
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", settings.strokeWidth || "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  if (settings.size) {
    svg.setAttribute("width", String(settings.size));
    svg.setAttribute("height", String(settings.size));
  }
  for (const entry of shape) {
    const node = document.createElementNS(SVG_NS, entry[0]);
    Object.entries(entry[1]).forEach((pair) => {
      node.setAttribute(pair[0], String(pair[1]));
    });
    svg.append(node);
  }
  return svg;
}
const SITE_TEST_STALE_TEXT = "站点配置已变化，请刷新后重新测试。";
const SAFE_TEST_ERRORS = new Map([
  ["站点配置已变化，请刷新后重试。", ["站点配置已变化，请刷新列表后重试。", "warning"]],
  ["站点配置已变化，请刷新后重新测试。", ["站点配置已变化，请刷新列表后重新测试。", "warning"]],
  ["同时进行的连接测试过多，请稍后重试。", ["同时进行的连接测试过多，请稍后手动重试。", "warning"]],
  ["测试连接超时。", ["连接上游超时，请稍后手动重试。", "danger"]],
  ["插件当前不可用。", ["插件当前不可用，请关闭后重新打开页面。", "danger"]],
  ["插件当前不可用，请重新打开页面后重试。", ["插件当前不可用，请重新打开页面后重试。", "danger"]],
  ["内容客户端当前不可用。", ["内容客户端当前不可用，请稍后手动重试。", "danger"]],
  ["站点存储不可用。", ["站点存储当前不可用，请稍后手动重试。", "danger"]],
  ["需要登录后才能获取内容预览。", ["登录状态无效，请重新登录 Dashboard。", "danger"]],
  ["连接上游失败。", ["无法连接上游，请检查站点地址后手动重试。", "danger"]],
  ["网络传输失败。", ["网络传输失败，请检查站点地址后手动重试。", "danger"]],
  ["上游返回了失败状态。", ["上游返回了失败状态。", "danger"]],
  ["插件当前未启用。", ["插件当前未启用，请启用插件后重新打开页面。", "danger"]],
  ["站点不存在。", ["站点不存在或已被删除，请刷新列表。", "danger"]],
  ["站点不存在或已被删除。", ["站点不存在或已被删除，请刷新列表。", "danger"]],
  ["内容类型无效。", ["所请求的内容类型无效。", "danger"]],
  ["revision 无效。", ["站点版本无效，请刷新列表。", "danger"]],
  ["测试参数无效。", ["测试参数无效，请刷新列表后重试。", "danger"]],
  ["请求体必须是 JSON 对象。", ["请求格式无效。", "danger"]],
  ["上游发生不允许的重定向。", ["上游返回了不允许的重定向。", "danger"]],
  ["上游响应过大。", ["上游响应超过大小限制。", "danger"]],
  ["上游返回非 JSON。", ["上游返回的内容不是有效 JSON。", "danger"]],
  ["上游业务状态无效。", ["上游业务状态无效。", "danger"]],
  ["上游内容结构无效。", ["上游内容结构不符合所选接口约定。", "danger"]],
  ["测试失败，无法完成连接验证。", ["测试失败，无法完成连接验证。", "danger"]],
]);

function safeSiteTestMessage(message) {
  const text = typeof message === "string" ? message.trim() : "";
  const safe = SAFE_TEST_ERRORS.get(text);
  if (safe) {
    return safe[0];
  }
  // 上游正文和凭据不回显：只给安全的通用说明。
  return "测试失败，无法完成连接验证。";
}

// 站点版本与启用状态一起决定一份测试结果是否还能贴到当前配置上。
function siteTestSignature(site) {
  return String(site.revision) + ":" + String(site.enabled);
}

function siteRowElement(siteId) {
  for (const row of Array.from(elements.sitesList.children)) {
    if (row.dataset.siteId === siteId) {
      return row;
    }
  }
  return null;
}

function currentSiteTest(site) {
  const record = state.siteTest.get(site.id);
  if (!record || record.signature !== siteTestSignature(site)) {
    return null;
  }
  return record;
}

function siteTestCell(site, type) {
  const record = currentSiteTest(site);
  const cell = record ? record.cells.get(type) : null;
  return cell && SITE_TEST_STATE_TEXT[cell.state] ? cell : { state: "idle" };
}

function siteTestCellTitle(label, cell) {
  const base = label + "：" + (SITE_TEST_STATE_TEXT[cell.state] || "未测试");
  if (typeof cell.message === "string" && cell.message) {
    const elapsed = Number.isFinite(cell.elapsed)
      ? "，耗时 " + cell.elapsed + " ms"
      : "";
    return base + "（" + cell.message + elapsed + "）";
  }
  return base;
}

function renderSiteTestBlock(site) {
  const block = document.createElement("div");
  block.className = "site-test-block";
  const chips = document.createElement("ul");
  chips.className = "site-test-chips";
  chips.setAttribute("aria-label", "五类内容接口测试状态");
  const notes = [];
  for (const entry of SITE_TEST_TYPES) {
    const type = entry[0];
    const label = entry[1];
    const cell = siteTestCell(site, type);
    const item = document.createElement("li");
    item.className = "site-test-chip";
    item.dataset.type = type;
    item.dataset.state = cell.state;
    const icon = document.createElement("span");
    icon.className = "site-test-icon";
    icon.dataset.state = cell.state;
    icon.setAttribute("aria-hidden", "true");
    icon.append(createSvgIcon(
      SITE_TEST_STATE_ICON[cell.state] || SITE_TEST_STATE_ICON.idle,
      { strokeWidth: "1.9" },
    ));
    const text = document.createElement("span");
    text.className = "site-test-text";
    text.textContent = label + " " + SITE_TEST_STATE_TEXT[cell.state];
    item.title = siteTestCellTitle(label, cell);
    item.append(icon, text);
    chips.append(item);
    if (cell.state === "error" || cell.state === "empty" || cell.state === "stale") {
      const detail = cell.message || SITE_TEST_STATE_TEXT[cell.state];
      const elapsed = Number.isFinite(cell.elapsed)
        ? "（耗时 " + cell.elapsed + " ms）"
        : "";
      notes.push(label + "：" + detail + elapsed);
    }
  }
  block.append(chips);
  if (notes.length > 0) {
    // 用 details 展开说明：手机点按与键盘聚焦都能看到，不依赖悬停提示。
    const details = document.createElement("details");
    details.className = "site-test-note site-test-details";
    const summary = document.createElement("summary");
    summary.textContent = "查看说明（" + notes.length + " 项）";
    const list = document.createElement("ul");
    list.className = "site-test-detail-list";
    for (const text of notes) {
      const item = document.createElement("li");
      item.textContent = text;
      list.append(item);
    }
    details.append(summary, list);
    block.append(details);
  }
  return block;
}

function updateSiteTestBlock(siteId) {
  const row = siteRowElement(siteId);
  const site = state.sites.find((item) => item.id === siteId);
  if (!row || !site) {
    return;
  }
  const block = row.querySelector(".site-test-block");
  if (block) {
    // 重绘期间保留用户已展开的说明，避免每次结果落回都收起。
    const previous = block.querySelector(".site-test-details");
    const wasOpen = Boolean(previous && previous.open);
    const next = renderSiteTestBlock(site);
    if (wasOpen) {
      const details = next.querySelector(".site-test-details");
      if (details) {
        details.open = true;
      }
    }
    block.replaceWith(next);
  }
}

function setSiteTestSummary(text) {
  const value = typeof text === "string" ? text.trim() : "";
  elements.siteTestSummary.textContent = value;
  elements.siteTestSummary.hidden = !value;
}

function siteTestIsRunning(record) {
  for (const cell of record.cells.values()) {
    if (cell.state === "running") {
      return true;
    }
  }
  return false;
}

function applySiteTestResult(site, record, type, result, message, elapsed, revision) {
  // 站点在测试期间被修改、删除或重新测试时，旧结果一律丢弃。
  if (state.siteTest.get(site.id) !== record) {
    return null;
  }
  if (record.signature !== siteTestSignature(site)) {
    return null;
  }
  const cell = record.cells.get(type);
  if (!cell) {
    return null;
  }
  // 后端按类型分别读版本：结果带的版本不是列表里的版本时，只标注需重新测试。
  const versionUnknown = revision === null || revision === undefined;
  const versionMatches = !versionUnknown && Number(revision) === Number(site.revision);
  const stateName = result === "stale" || (!versionUnknown && !versionMatches)
    ? "stale"
    : result === "success" ? "success"
      : result === "empty" ? "empty"
        : "error";
  cell.state = stateName;
  if (stateName === "stale") {
    cell.message = SITE_TEST_STALE_TEXT;
  } else {
    cell.message = typeof message === "string"
      ? (stateName === "error" ? safeSiteTestMessage(message) : message.trim())
      : "";
  }
  cell.elapsed = Number.isFinite(Number(elapsed)) ? Number(elapsed) : null;
  updateSiteTestBlock(site.id);
  return stateName;
}

function findSite(siteId) {
  return state.sites.find((item) => item.id === siteId) || null;
}

async function runSiteTests(siteIds) {
  const targets = [];
  for (const id of siteIds) {
    const site = findSite(id);
    if (!site) {
      continue;
    }
    const record = currentSiteTest(site);
    if (record && siteTestIsRunning(record)) {
      continue;
    }
    targets.push(site);
  }
  if (targets.length === 0 || !bridge) {
    return;
  }

  const records = new Map();
  for (const site of targets) {
    const cells = new Map();
    for (const entry of SITE_TEST_TYPES) {
      cells.set(entry[0], { state: "running", message: "", elapsed: null });
    }
    const record = { signature: siteTestSignature(site), cells };
    state.siteTest.set(site.id, record);
    records.set(site.id, record);
    updateSiteTestBlock(site.id);
  }

  const batched = targets.length > 1;
  if (batched) {
    state.siteTestBusy = true;
    refreshSiteToolbar();
  }
  setSiteTestSummary(
    "正在测试 " + targets.length + " 个站点 × " + SITE_TEST_TYPES.length + " 类接口…",
  );

  let skipped = 0;
  try {
    for (let start = 0; start < targets.length; start += SITE_TEST_CHUNK) {
      const chunk = targets.slice(start, start + SITE_TEST_CHUNK);
      const chunkIds = [];
      const expected = {};
      for (const site of chunk) {
        const record = records.get(site.id);
        const current = findSite(site.id);
        if (!current || !record || state.siteTest.get(site.id) !== record ||
            record.signature !== siteTestSignature(current)) {
          skipped += 1;
          continue;
        }
        chunkIds.push(site.id);
        // 把发起时看到的版本一起提交，后端据此判定结果是否还属于当前配置。
        expected[site.id] = Number(current.revision);
      }
      if (chunkIds.length === 0) {
        continue;
      }
      let results = [];
      try {
        const data = await bridge.apiPost("sites/test-batch", {
          site_ids: chunkIds,
          content_types: SITE_TEST_TYPES.map((entry) => entry[0]),
          expected_revisions: expected,
        });
        const payload = data && data.data ? data.data : (data || {});
        results = Array.isArray(payload.results) ? payload.results : [];
      } catch (error) {
        // 这一块整体失败时逐站标注，后面的块继续测试。
        const message = friendlyErrorMessage(error);
        for (const id of chunkIds) {
          const site = findSite(id);
          const record = records.get(id);
          if (!site || !record) {
            continue;
          }
          for (const entry of SITE_TEST_TYPES) {
            applySiteTestResult(site, record, entry[0], "error", message, null);
          }
        }
        continue;
      }
      const seen = new Set();
      for (const item of results) {
        if (!item || typeof item !== "object") {
          continue;
        }
        const site = findSite(item.site_id);
        const record = records.get(item.site_id);
        if (!site || !record) {
          continue;
        }
        seen.add(String(item.site_id) + "|" + String(item.content_type));
        applySiteTestResult(
          site, record, item.content_type, item.result, item.message,
          item.elapsed_ms, item.site_revision,
        );
      }
      // 后端没有返回的类型也要收尾，不能一直停在“测试中”。
      for (const id of chunkIds) {
        const site = findSite(id);
        const record = records.get(id);
        if (!site || !record) {
          continue;
        }
        for (const entry of SITE_TEST_TYPES) {
          if (seen.has(id + "|" + entry[0])) {
            continue;
          }
          applySiteTestResult(
            site, record, entry[0], "error", "没有收到该接口的测试结果。", null,
          );
        }
      }
    }
  } finally {
    if (batched) {
      state.siteTestBusy = false;
      refreshSiteToolbar();
    }
  }

  const tally = { success: 0, empty: 0, error: 0, stale: 0 };
  for (const site of targets) {
    const record = records.get(site.id);
    if (!record) {
      continue;
    }
    for (const cell of record.cells.values()) {
      if (tally[cell.state] !== undefined) {
        tally[cell.state] += 1;
      }
    }
  }
  let summary = "测试完成：" + targets.length + " 个站点 × "
    + SITE_TEST_TYPES.length + " 类；成功 " + tally.success + " 项，"
    + "合法空内容 " + tally.empty + " 项，失败 " + tally.error + " 项";
  if (tally.stale > 0) {
    summary += "；另有 " + tally.stale + " 项因站点配置已变化未采用，请刷新后重新测试";
  }
  summary += "。";
  if (skipped > 0) {
    summary += "另有 " + skipped + " 个站点在测试期间配置变化，结果已作废，请重新测试。";
  }
  setSiteTestSummary(summary);
}

const SITE_ACTION_ICONS = {
  test: "M6.6 18.2a4.4 4.4 0 0 1-.7-8.74 5 5 0 0 1 9.6-.9 3.9 3.9 0 0 1 1 7.64M12 12.2v7.2M9.2 16.6 12 19.4l2.8-2.8",
  edit: "M4.5 19.5h4L19 9l-4-4L4.5 15.5v4ZM13.8 6.2l4 4",
  delete:
    "M4.6 7.2h14.8M9.5 7.2V5.5a1.3 1.3 0 0 1 1.3-1.3h2.4a1.3 1.3 0 0 1 1.3 1.3v1.7"
    + "M6.7 7.2l.66 11.1a1.7 1.7 0 0 0 1.7 1.6h5.88a1.7 1.7 0 0 0 1.7-1.6l.66-11.1"
    + "M10.4 10.7v5.9M13.6 10.7v5.9",
};

function siteActionIcon(kind) {
  return createSvgIcon(
    [["path", { d: SITE_ACTION_ICONS[kind] || SITE_ACTION_ICONS.test }]],
    { size: 18 },
  );
}

function siteIconButton(kind, ariaLabel, onClick, extraClass) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = extraClass
    ? "icon-button site-icon-button " + extraClass
    : "icon-button site-icon-button";
  button.setAttribute("aria-label", ariaLabel);
  button.title = ariaLabel;
  button.append(siteActionIcon(kind));
  button.addEventListener("click", onClick);
  return button;
}

function siteSourcePill(site) {
  const pill = document.createElement("span");
  pill.className = "badge badge-source";
  if (site.source_kind === SOURCE_KIND_PUBLIC) {
    pill.dataset.kind = "public";
    pill.textContent = "公开";
  } else if (site.source_kind === SOURCE_KIND_SELF_BUILT) {
    pill.dataset.kind = "self_built";
    pill.textContent = "自建";
  } else {
    pill.dataset.kind = "unknown";
    pill.textContent = "来源未知";
  }
  return pill;
}

function renderSite(site) {
  const row = document.createElement("li");
  row.className = "site-row";
  row.dataset.siteId = site.id;
  row.dataset.enabled = String(site.enabled);
  row.dataset.selected = String(state.siteSelection.has(site.id));

  const select = document.createElement("input");
  select.type = "checkbox";
  select.className = "site-select";
  select.checked = row.dataset.selected === "true";
  select.setAttribute("aria-label", "选择站点 " + site.name);
  select.addEventListener("change", () => {
    if (select.checked) {
      state.siteSelection.add(site.id);
    } else {
      state.siteSelection.delete(site.id);
    }
    row.dataset.selected = String(select.checked);
    refreshSiteToolbar();
  });

  const main = document.createElement("div");
  main.className = "site-main";

  const titleLine = document.createElement("div");
  titleLine.className = "site-title-line";
  const name = document.createElement("h3");
  name.className = "site-name";
  name.textContent = site.name;
  name.title = site.name;
  titleLine.append(siteSourcePill(site), name);
  if (!site.enabled) {
    const badge = document.createElement("span");
    badge.className = "badge badge-disabled";
    badge.textContent = "已停用";
    titleLine.append(badge);
  }

  // 名称与地址同一行：名称按输入上限截断，地址过长时省略号收尾，完整值在编辑界面可见。
  const url = document.createElement("span");
  url.className = "site-url";
  url.textContent = site.base_url;
  url.title = site.base_url;
  titleLine.append(url);

  main.append(titleLine, renderSiteTestBlock(site));

  const actions = document.createElement("div");
  actions.className = "site-actions";
  actions.append(
    siteIconButton(
      "test",
      "测试站点 " + site.name + " 的五类接口",
      () => runSiteTests([site.id]),
    ),
    siteIconButton("edit", "编辑站点 " + site.name, () => openEdit(site)),
    siteIconButton(
      "delete",
      "删除站点 " + site.name,
      () => openDelete(site),
      "site-icon-danger",
    ),
  );

  row.append(select, main, actions);
  return row;
}

function pruneSiteSelection() {
  const alive = new Set(state.sites.map((site) => site.id));
  for (const id of Array.from(state.siteSelection)) {
    if (!alive.has(id)) {
      state.siteSelection.delete(id);
    }
  }
}

function syncSiteCheckboxes() {
  for (const row of Array.from(elements.sitesList.children)) {
    const box = row.querySelector(".site-select");
    if (!box) {
      continue;
    }
    box.checked = state.siteSelection.has(row.dataset.siteId);
    row.dataset.selected = String(box.checked);
  }
}

function refreshSiteToolbar() {
  const hasSites = state.sites.length > 0;
  const count = state.sites.filter(
    (site) => state.siteSelection.has(site.id),
  ).length;
  elements.siteSelection.hidden = !hasSites;
  elements.siteSelectionCount.textContent = "已选 " + count + " 个";
  elements.siteSelectAll.disabled = !hasSites;
  elements.siteSelectNone.disabled = !hasSites;
  elements.siteSelectInvert.disabled = !hasSites;
  elements.siteTestSelected.disabled = count === 0 || state.siteTestBusy;
  elements.siteDeleteSelected.disabled = count === 0 || state.deleting;
  elements.siteTestSelected.textContent = state.siteTestBusy ? "测试中…" : "批量测试";
}

function selectAllSites() {
  for (const site of state.sites) {
    state.siteSelection.add(site.id);
  }
  syncSiteCheckboxes();
  refreshSiteToolbar();
}

function clearSiteSelection() {
  state.siteSelection.clear();
  syncSiteCheckboxes();
  refreshSiteToolbar();
}

function invertSiteSelection() {
  for (const site of state.sites) {
    if (state.siteSelection.has(site.id)) {
      state.siteSelection.delete(site.id);
    } else {
      state.siteSelection.add(site.id);
    }
  }
  syncSiteCheckboxes();
  refreshSiteToolbar();
}

function selectedSiteIds() {
  return state.sites
    .filter((site) => state.siteSelection.has(site.id))
    .map((site) => site.id);
}

function renderSites() {
  elements.siteCount.textContent = state.sites.length + " 个";
  elements.sitesList.replaceChildren();
  const alive = new Set(state.sites.map((site) => site.id));
  for (const id of Array.from(state.siteTest.keys())) {
    if (!alive.has(id)) {
      state.siteTest.delete(id);
    }
  }
  for (const site of state.sites) {
    const record = state.siteTest.get(site.id);
    if (record && record.signature !== siteTestSignature(site)) {
      state.siteTest.delete(site.id);
    }
  }

  if (state.sites.length === 0) {
    elements.sitesList.hidden = true;
    elements.emptyState.hidden = false;
    clearListState();
    setSiteTestSummary("");
    refreshSiteToolbar();
    return;
  }

  elements.emptyState.hidden = true;
  elements.sitesList.hidden = false;
  clearListState();
  for (const site of state.sites) {
    elements.sitesList.append(renderSite(site));
  }
  refreshSiteToolbar();
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

function resetPreviewImages() {
  state.taskPreviewImageRequestId += 1;
  state.taskPreviewImageLoading = false;
  state.taskPreviewSnapshotId = null;
  const hadViewer = Boolean(
    elements.previewImageViewer && elements.previewImageViewer.open,
  );
  elements.taskPreviewImagePages.replaceChildren();
  closePreviewImageViewer(false);
  elements.taskPreviewImageState.textContent = "";
  elements.taskPreviewImageState.hidden = true;
  elements.taskPreviewImageHint.textContent = "";
  elements.taskPreviewImageHint.hidden = true;
  syncPreviewImageButton();
  if (hadViewer) {
    // 浮层因预览刷新被收起时，按钮可能同时被禁用；把焦点交给视图标题，
    // 避免焦点落到 body 或已关闭的浮层里。
    const heading = document.querySelector("#view-tasks h1");
    if (heading) {
      if (!heading.hasAttribute("tabindex")) {
        heading.setAttribute("tabindex", "-1");
      }
      heading.focus({ preventScroll: true });
    }
  }
}

function syncPreviewImageButton() {
  const hasImageContext = Boolean(
    state.taskPreviewOutputs &&
    state.taskPreviewSnapshotId &&
    state.taskPreviewTaskId,
  );
  elements.taskPreviewImageButton.disabled =
    state.taskPreviewLoading ||
    state.taskPreviewImageLoading ||
    !hasImageContext;
  elements.taskPreviewImageButton.textContent =
    state.taskPreviewImageLoading ? "正在生成…" : "生成长图";
}

const PREVIEW_IMAGE_STATUS_LABELS = {
  success: "已生成",
  empty: "空栏目",
  failed: "未生成",
};

// 栏目长图失败原因：渲染器给出具体上限时直接可行动；历史记录只有
// 通用 image_failed，仍按旧文案兼容。
const SECTION_IMAGE_ERROR_LABELS = {
  section_too_tall: "图片高度超限",
  section_too_large: "图片体积超限",
  section_too_long: "栏目内容超出字符上限",
  section_failed: "栏目获取失败",
  total_bytes_exceeded: "图片总量超限",
  image_failed: "图片生成失败",
  render_failed: "渲染失败",
};

function sectionImageErrorLabel(code) {
  if (!code) {
    return "";
  }
  return SECTION_IMAGE_ERROR_LABELS[code] || String(code);
}

const PREVIEW_IMAGE_VIEWER_CLASS = "preview-image-viewer-open";
const PREVIEW_IMAGE_VIEWER_HINT =
  "已按屏幕宽度显示完整长图；需要细节时点“原始尺寸”。";
const PREVIEW_IMAGE_VIEWER_HINT_ACTUAL =
  "原始 900px 显示，窄屏可左右滑动查看细节。";

function previewImageViewerFocusFallback() {
  const candidates = [
    elements.taskPreviewImageButton,
    elements.taskPreviewRefreshButton,
    elements.taskPreviewDoneButton,
  ];
  for (const candidate of candidates) {
    if (candidate && !candidate.disabled && !candidate.hidden) {
      return candidate;
    }
  }
  // 预览进行中时上述控件都可能被禁用，退到当前视图标题，避免焦点落到 body。
  const heading = document.querySelector("#view-tasks h1");
  if (heading) {
    if (!heading.hasAttribute("tabindex")) {
      heading.setAttribute("tabindex", "-1");
    }
    return heading;
  }
  return null;
}

function applyPreviewImageViewerZoom(actual) {
  state.imageViewerActual = Boolean(actual);
  const body = elements.previewImageViewerBody;
  if (body) body.classList.toggle("is-actual", state.imageViewerActual);
  const button = elements.previewImageViewerZoom;
  if (button) {
    button.setAttribute("aria-pressed", String(state.imageViewerActual));
    button.textContent = state.imageViewerActual ? "适应宽度" : "原始尺寸";
  }
  elements.previewImageViewerHint.textContent = state.imageViewerActual
    ? PREVIEW_IMAGE_VIEWER_HINT_ACTUAL
    : PREVIEW_IMAGE_VIEWER_HINT;
}

function openPreviewImageViewer(section, trigger) {
  const viewer = elements.previewImageViewer;
  if (!viewer || typeof viewer.showModal !== "function" || viewer.open) {
    return;
  }
  if (!section || typeof section.png_base64 !== "string" || !section.png_base64) {
    return;
  }
  state.imageViewerSection = section;
  state.imageViewerTrigger = trigger || null;
  state.imageViewerScrollY = window.scrollY;
  elements.previewImageViewerTitle.textContent = section.index + ". " + section.label;
  elements.previewImageViewerImg.src = "data:image/png;base64," + section.png_base64;
  elements.previewImageViewerImg.width = section.width;
  elements.previewImageViewerImg.height = section.height;
  elements.previewImageViewerImg.alt = section.label + "栏目长图（大图）";
  applyPreviewImageViewerZoom(false);
  elements.previewImageViewerDownload.textContent = "下载「" + section.label + "」PNG";
  elements.previewImageViewerDownload.setAttribute(
    "aria-label",
    "下载" + section.label + "栏目长图",
  );
  document.documentElement.classList.add(PREVIEW_IMAGE_VIEWER_CLASS);
  elements.previewImageViewerBody.scrollTop = 0;
  elements.previewImageViewerBody.scrollLeft = 0;
  viewer.showModal();
  elements.previewImageViewerClose.focus();
}

function closePreviewImageViewer(restoreFocus = true) {
  document.documentElement.classList.remove(PREVIEW_IMAGE_VIEWER_CLASS);
  const viewer = elements.previewImageViewer;
  if (!viewer || !viewer.open) {
    return;
  }
  const trigger = state.imageViewerTrigger;
  const scrollY = state.imageViewerScrollY;
  state.imageViewerSection = null;
  state.imageViewerTrigger = null;
  viewer.close();
  elements.previewImageViewerImg.removeAttribute("src");
  elements.previewImageViewerImg.alt = "";
  elements.previewImageViewerImg.width = 900;
  elements.previewImageViewerImg.height = 0;
  applyPreviewImageViewerZoom(false);
  if (restoreFocus) {
    const target = trigger && document.contains(trigger)
      ? trigger
      : previewImageViewerFocusFallback();
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

bindPreviewImageViewer();

function bindPreviewImageViewer() {
  const viewer = elements.previewImageViewer;
  if (!viewer) {
    return;
  }
  elements.previewImageViewerClose?.addEventListener("click", () => {
    closePreviewImageViewer(true);
  });
  elements.previewImageViewerZoom?.addEventListener("click", () => {
    applyPreviewImageViewerZoom(!state.imageViewerActual);
  });
  elements.previewImageViewerDownload?.addEventListener("click", () => {
    if (state.imageViewerSection) {
      downloadPreviewImageSection(state.imageViewerSection);
    }
  });
  viewer.addEventListener("cancel", (event) => {
    event.preventDefault();
    closePreviewImageViewer(true);
  });
  viewer.addEventListener("click", (event) => {
    if (event.target === viewer) {
      closePreviewImageViewer(true);
    }
  });
}

function renderPreviewImages(data) {
  if (!data || !Array.isArray(data.images) || data.images.length === 0) {
    throw new Error("图片数据格式异常。");
  }
  elements.taskPreviewImagePages.replaceChildren();
  closePreviewImageViewer(true);
  let generated = 0;
  let missing = 0;
  for (const section of data.images) {
    if (
      !section ||
      !Number.isInteger(section.index) ||
      typeof section.content_type !== "string" ||
      typeof section.label !== "string" ||
      !section.label ||
      !PREVIEW_IMAGE_STATUS_LABELS[section.status]
    ) {
      throw new Error("栏目长图数据格式异常。");
    }
    const figure = document.createElement("figure");
    figure.className = "preview-image-page";
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
        openPreviewImageViewer(section, image);
      });
      image.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
          event.preventDefault();
          openPreviewImageViewer(section, image);
        }
      });
      figure.append(image);
      generated += 1;
    } else {
      missing += 1;
    }
    const caption = document.createElement("figcaption");
    if (hasImage) {
      const note = typeof section.note === "string" && section.note
        ? " · " + section.note
        : "";
      caption.textContent =
        section.index + ". " + section.label + " · " +
        section.width + "×" + section.height + " · " +
        PREVIEW_IMAGE_STATUS_LABELS[section.status] + note;
    } else {
      caption.className = "task-disabled";
      const reason = sectionImageErrorLabel(
        typeof section.error === "string" ? section.error : "",
      ) || "未生成";
      caption.textContent =
        section.index + ". " + section.label + " · 未生成（" + reason + "）";
    }
    figure.append(caption);
    if (hasImage) {
      const actions = document.createElement("div");
      actions.className = "preview-image-actions";
      const download = makeButton(
        "下载「" + section.label + "」PNG",
        "button button-secondary",
        () => downloadPreviewImageSection(section),
        "下载" + section.label + "栏目长图",
      );
      const view = makeButton(
        "查看大图",
        "button button-secondary",
        () => openPreviewImageViewer(section, view),
        "查看" + section.label + "大图",
      );
      actions.append(download, view);
      figure.append(actions);
    }
    elements.taskPreviewImagePages.append(figure);
  }
  elements.taskPreviewImageState.textContent = missing
    ? "已生成 " + generated + " 张栏目长图，" + missing + " 个栏目未生成"
    : "已生成 " + generated + " 张栏目长图";
  elements.taskPreviewImageState.hidden = false;
  elements.taskPreviewImageHint.textContent = missing
    ? "未生成的栏目没有可用图片；如需完整出图，请减少栏目条数后重新预览再生成。"
    : "";
  elements.taskPreviewImageHint.hidden = !missing;
}

function downloadPreviewImageSection(section) {
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
    const safeId = String(state.taskPreviewTaskId || "task")
      .replace(/[^A-Za-z0-9_-]/g, "")
      .slice(0, 64) || "task";
    const safeType = String(section.content_type || "section")
      .replace(/[^A-Za-z0-9_-]/g, "")
      .slice(0, 32) || "section";
    const filename =
      "60sapi-task-" + safeId + "-" + safeType + "-" + section.index + ".png";
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
    elements.taskPreviewImageHint.textContent = "已下载 " + filename + "。";
    elements.taskPreviewImageHint.hidden = false;
  } catch {
    elements.taskPreviewImageHint.textContent = "图片下载失败，请重新生成。";
    elements.taskPreviewImageHint.hidden = false;
  }
}

async function generatePreviewImage() {
  if (
    !bridge ||
    state.taskPreviewImageLoading ||
    state.taskPreviewLoading ||
    !state.taskPreviewSnapshotId ||
    !state.taskPreviewOutputs
  ) {
    return;
  }
  const taskId = state.taskPreviewTaskId;
  const snapshotId = state.taskPreviewSnapshotId;
  const requestId = ++state.taskPreviewImageRequestId;
  state.taskPreviewImageLoading = true;
  elements.taskPreviewImagePages.replaceChildren();
  elements.taskPreviewImageHint.textContent = "";
  elements.taskPreviewImageHint.hidden = true;
  elements.taskPreviewImageState.textContent = "正在生成图片…";
  elements.taskPreviewImageState.hidden = false;
  syncPreviewImageButton();
  try {
    const data = await bridge.apiPost(
      `tasks/${encodeURIComponent(taskId)}/image`,
      { snapshot_id: snapshotId },
    );
    if (
      requestId !== state.taskPreviewImageRequestId ||
      snapshotId !== state.taskPreviewSnapshotId ||
      taskId !== state.taskPreviewTaskId ||
      !state.taskPreviewOutputs
    ) {
      return;
    }
    renderPreviewImages(data);
  } catch (error) {
    if (requestId === state.taskPreviewImageRequestId) {
      elements.taskPreviewImageState.textContent = "";
      elements.taskPreviewImageState.hidden = true;
      elements.taskPreviewImageHint.textContent =
        `图片生成失败：${friendlyErrorMessage(error)}；文字和 Markdown 仍可使用。`;
      elements.taskPreviewImageHint.hidden = false;
    }
  } finally {
    if (requestId === state.taskPreviewImageRequestId) {
      state.taskPreviewImageLoading = false;
      syncPreviewImageButton();
    }
  }
}

function resetPreviewOutput() {
  state.taskPreviewOutputEpoch += 1;
  state.taskPreviewOutputs = null;
  state.taskPreviewFormat = "text";
  elements.taskPreviewOutputText.value = "";
  elements.taskPreviewOutputHint.textContent = "";
  elements.taskPreviewOutputHint.hidden = true;
  const textRadio = elements.taskPreviewRoot?.querySelector(
    'input[name="task-preview-output-format"][value="text"]',
  );
  if (textRadio) {
    textRadio.checked = true;
  }
  syncPreviewOutputButtons();
}

function selectedPreviewOutput() {
  if (!state.taskPreviewOutputs) {
    return "";
  }
  return state.taskPreviewFormat === "markdown"
    ? state.taskPreviewOutputs.markdown
    : state.taskPreviewOutputs.text;
}

function syncPreviewOutputButtons() {
  const hasOutput = Boolean(selectedPreviewOutput());
  elements.taskPreviewCopyButton.disabled =
    state.taskPreviewLoading || !hasOutput;
  elements.taskPreviewDownloadButton.disabled =
    state.taskPreviewLoading || !hasOutput;
  elements.taskPreviewDownloadButton.textContent =
    state.taskPreviewFormat === "markdown" ? "下载 .md" : "下载 .txt";
}

function renderPreviewOutput() {
  state.taskPreviewOutputEpoch += 1;
  const output = selectedPreviewOutput();
  elements.taskPreviewOutputText.value = output;
  elements.taskPreviewOutputHint.textContent = "";
  elements.taskPreviewOutputHint.hidden = true;
  syncPreviewOutputButtons();
}

async function copyPreviewOutput() {
  const output = selectedPreviewOutput();
  if (!output || state.taskPreviewLoading) {
    return;
  }
  const operation = {
    id: ++state.taskPreviewCopyOperationId,
    epoch: state.taskPreviewOutputEpoch,
    format: state.taskPreviewFormat,
    text: output,
  };
  const ownsOperation = () =>
    operation.id === state.taskPreviewCopyOperationId &&
    operation.epoch === state.taskPreviewOutputEpoch &&
    operation.format === state.taskPreviewFormat &&
    operation.text === selectedPreviewOutput();
  try {
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      throw new Error("clipboard unavailable");
    }
    await navigator.clipboard.writeText(output);
    if (!ownsOperation()) {
      return;
    }
    elements.taskPreviewOutputHint.textContent =
      `已复制${operation.format === "markdown" ? " Markdown 源码" : "纯文字"}。`;
    elements.taskPreviewOutputHint.hidden = false;
  } catch {
    if (!ownsOperation()) {
      return;
    }
    elements.taskPreviewOutputText.focus();
    elements.taskPreviewOutputText.select();
    elements.taskPreviewOutputHint.textContent =
      "无法访问剪贴板，已选中当前文本，请手动复制。";
    elements.taskPreviewOutputHint.hidden = false;
  }
}

function downloadPreviewOutput() {
  const output = selectedPreviewOutput();
  if (!output || state.taskPreviewLoading) {
    return;
  }
  state.taskPreviewOutputEpoch += 1;
  const extension = state.taskPreviewFormat === "markdown" ? "md" : "txt";
  const safeId = String(state.taskPreviewTaskId || "task")
    .replace(/[^A-Za-z0-9_-]/g, "")
    .slice(0, 64) || "task";
  const filename = `60sapi-task-${safeId}.${extension}`;
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
    elements.taskPreviewOutputHint.textContent = `已下载 ${filename}。`;
    elements.taskPreviewOutputHint.hidden = false;
  } catch {
    elements.taskPreviewOutputHint.textContent = "无法生成下载文件。";
    elements.taskPreviewOutputHint.hidden = false;
  }
}

function previewSectionCacheText(section) {
  const cache = section.cache && typeof section.cache === "object"
    ? section.cache
    : {};
  return cache.hit === true ? "缓存命中" : "本次上游获取";
}

function appendPreviewSectionItems(container, section) {
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

function renderPreviewSection(section) {
  const article = document.createElement("article");
  article.className = "preview-block";
  article.dataset.status = section.status === "error" ? "error" : "ok";
  const head = document.createElement("div");
  head.className = "preview-block-head";
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
    error.className = "preview-block-error";
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
  addMeta(meta, "缓存", previewSectionCacheText(section));
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
  appendPreviewSectionItems(article, section);
  return article;
}

/* ---------- 任务内容栏目：类型/条数/顺序都在任务表单里维护 ---------- */

const TASK_ITEM_TYPES = Object.keys(CONTENT_LABELS);
const TASK_ITEM_MAX = TASK_ITEM_TYPES.length;
const TASK_ITEM_LIMIT_MAX = 1000;

function taskItemLabel(contentType) {
  return CONTENT_LABELS[contentType] || contentType;
}

function taskItemLimitText(limit) {
  return Number.isInteger(limit) ? String(limit) : "";
}

function savedTaskItems(task) {
  const list = task && Array.isArray(task.items) ? task.items : [];
  return list
    .filter((item) => item && typeof item.content_type === "string")
    .map((item) => ({
      content_type: item.content_type,
      limit: Number.isInteger(item.limit) ? item.limit : null,
    }));
}

function taskItemOptions() {
  const used = new Set(state.taskItems.map((item) => item.content_type));
  const available = TASK_ITEM_TYPES.filter((type) => !used.has(type));
  elements.taskItemPicker.replaceChildren();
  if (available.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "五个栏目都已添加";
    elements.taskItemPicker.append(option);
    elements.taskItemPicker.disabled = true;
    elements.taskItemAddButton.disabled = true;
    return;
  }
  elements.taskItemPicker.disabled = false;
  elements.taskItemAddButton.disabled = false;
  for (const type of available) {
    const option = document.createElement("option");
    option.value = type;
    option.textContent = taskItemLabel(type);
    elements.taskItemPicker.append(option);
  }
}

function renderTaskItems() {
  elements.taskItemList.replaceChildren();
  if (state.taskItems.length === 0) {
    const empty = document.createElement("li");
    empty.className = "task-item-empty";
    empty.textContent = "还没有栏目：请至少添加一个。栏目顺序即推送顺序。";
    elements.taskItemList.append(empty);
  }
  state.taskItems.forEach((item, index) => {
    const row = document.createElement("li");
    row.className = "task-item-row";
    row.dataset.contentType = item.content_type;
    const order = document.createElement("span");
    order.className = "task-item-order";
    order.textContent = String(index + 1);
    const label = document.createElement("span");
    label.className = "task-item-label";
    label.textContent = taskItemLabel(item.content_type);
    const limitField = document.createElement("label");
    limitField.className = "task-item-limit";
    const limitInput = document.createElement("input");
    limitInput.type = "number";
    limitInput.min = "1";
    limitInput.max = String(TASK_ITEM_LIMIT_MAX);
    limitInput.step = "1";
    limitInput.inputMode = "numeric";
    limitInput.className = "task-item-limit-input";
    limitInput.value = item.limitText;
    limitInput.dataset.itemIndex = String(index);
    limitInput.setAttribute("aria-label", taskItemLabel(item.content_type) + "条数");
    limitInput.addEventListener("input", () => applyTaskItemLimitEdit(limitInput, index));
    // 退格/删除是明确的「清空」：数字框里的未完成数字（"1e"、"-"）浏览器清不掉，
    // 必须手动复位。Escape 不在此列，它只承担关闭对话框的语义。
    limitInput.addEventListener("keydown", (event) => {
      if (!limitInput.validity.badInput && !item.limitBadInput) return;
      if (event.key !== "Backspace" && event.key !== "Delete") return;
      limitInput.value = "";
      applyTaskItemLimitEdit(limitInput, index);
    });
    const limitText = document.createElement("span");
    limitText.className = "task-item-limit-text";
    limitText.textContent = "条";
    const limitHint = document.createElement("span");
    limitHint.className = "task-item-limit-hint";
    limitHint.hidden = true;
    limitField.append(limitInput, limitText);
    const actions = document.createElement("span");
    actions.className = "task-item-actions";
    const up = makeButton("上移", "button button-secondary",
      () => moveTaskItem(index, -1), "上移栏目 " + taskItemLabel(item.content_type));
    const down = makeButton("下移", "button button-secondary",
      () => moveTaskItem(index, 1), "下移栏目 " + taskItemLabel(item.content_type));
    up.disabled = index === 0;
    down.disabled = index === state.taskItems.length - 1;
    actions.append(up, down,
      makeButton("移除", "button button-quiet-danger",
        () => removeTaskItem(index), "移除栏目 " + taskItemLabel(item.content_type)),
    );
    row.append(order, label, limitField, actions, limitHint);
    // 提示节点要先挂进行里，sync 才找得到它。
    syncTaskItemLimitValidity(limitInput, item);
    elements.taskItemList.append(row);
  });
  taskItemOptions();
  invalidateTaskFormRequests();
  updateTaskFormGates();
}

function addTaskItem() {
  const contentType = elements.taskItemPicker.value;
  if (!contentType) return;
  if (state.taskItems.some((item) => item.content_type === contentType)) return;
  if (state.taskItems.length >= TASK_ITEM_MAX) return;
  // 新栏目插到末尾：表单里的顺序就是推送顺序。
  state.taskItems.push({ content_type: contentType, limitText: "", limitBadInput: false });
  renderTaskItems();
}

function moveTaskItem(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= state.taskItems.length) return;
  const [item] = state.taskItems.splice(index, 1);
  state.taskItems.splice(target, 0, item);
  renderTaskItems();
}

function removeTaskItem(index) {
  state.taskItems.splice(index, 1);
  renderTaskItems();
}

function collectTaskItems() {
  const seen = new Set();
  const items = [];
  for (const entry of currentTaskItemEntries()) {
    if (seen.has(entry.content_type)) {
      throw new Error("同一栏目只能添加一次。");
    }
    seen.add(entry.content_type);
    if (entry.kind === "invalid") {
      throw new Error(taskItemLimitErrorMessage([taskItemLabel(entry.content_type)]));
    }
    items.push({
      content_type: entry.content_type,
      limit: entry.kind === "limit" ? entry.limit : null,
    });
  }
  return items;
}

/* ---------- 任务预览：POST tasks/<id>/preview ---------- */

function resetTaskPreviewImages() {
  // 预览重新获取后旧快照失效：长图必须重新生成。
  state.taskPreviewSnapshotId = null;
  state.taskPreviewImageRequestId += 1;
  state.taskPreviewImageLoading = false;
  elements.taskPreviewImagePages.replaceChildren();
  elements.taskPreviewImageState.textContent = "";
  elements.taskPreviewImageState.hidden = true;
  elements.taskPreviewImageHint.textContent = "";
  elements.taskPreviewImageHint.hidden = true;
  closePreviewImageViewer(true);
  syncPreviewImageButton();
}

function clearTaskPreviewResults() {
  elements.taskPreviewSections.replaceChildren();
  elements.taskPreviewFailed.replaceChildren();
  elements.taskPreviewFailed.hidden = true;
  elements.taskPreviewMeta.replaceChildren();
  elements.taskPreviewMeta.hidden = true;
  resetPreviewOutput();
  resetTaskPreviewImages();
}

function clearTaskPreviewPanels() {
  state.taskPreviewRequestId += 1;
  state.taskPreviewLoading = false;
  state.taskPreviewOutputs = null;
  state.taskPreviewTaskRevision = null;
  clearTaskPreviewResults();
  elements.taskPreviewStatus.textContent = "";
  elements.taskPreviewStatus.hidden = true;
  elements.taskPreviewStatus.className = "status preview-status";
  setTaskPreviewBusy(false);
}

function showTaskPreviewStatus(text, tone) {
  const node = elements.taskPreviewStatus;
  if (!text) {
    node.textContent = "";
    node.hidden = true;
    node.className = "status preview-status";
    return;
  }
  node.hidden = false;
  node.textContent = text;
  node.className = "status preview-status preview-status-" + (tone || "info");
}

function setTaskPreviewBusy(busy) {
  state.taskPreviewLoading = busy;
  elements.taskPreviewRefreshButton.disabled = busy;
  elements.taskPreviewRefreshButton.textContent = busy ? "正在获取…" : "重新获取";
  syncPreviewOutputButtons();
  syncPreviewImageButton();
}

function taskPreviewErrorText(error) {
  const detail = error && typeof error === "object" ? error : {};
  const payload = detail.data && typeof detail.data === "object" ? detail.data : {};
  const code = String(detail.code || payload.code || "");
  const message = friendlyErrorMessage(error);
  if (code === "content_unavailable" || message.includes("没有候选站点能同时提供全部栏目")) {
    return "没有候选站点能同时提供全部栏目，请调整栏目或候选站点后再预览。";
  }
  if (code === "preview_revision_changed" || message.includes("任务配置在获取期间发生变化")) {
    return "任务配置在获取期间发生变化，本次预览已取消，请重试。";
  }
  return "预览失败：" + message;
}

function renderTaskPreviewMeta(data) {
  const meta = elements.taskPreviewMeta;
  meta.replaceChildren();
  addMeta(meta, "任务", data.task_name || "未命名任务");
  addMeta(meta, "站点", data.site_name || "未记录");
  const items = Array.isArray(data.items) ? data.items : [];
  addMeta(meta, "栏目", items.length > 0
    ? items.map((item) => {
      const label = item.label || taskItemLabel(item.content_type);
      return Number.isInteger(item.limit) ? label + "（" + item.limit + " 条）" : label;
    }).join("、")
    : "未记录");
  if (data.image_style) {
    addMeta(meta, "图片样式", taskStyleName(data.image_style));
  }
  const sections = Array.isArray(data.sections) ? data.sections : [];
  const source = sections.find((section) =>
    section && typeof section.source_display === "string" && section.source_display);
  if (source) {
    // source_display 已按隐私规则处理：自建服务只显示标签，不含地址与凭据。
    addMeta(meta, "来源", source.source_display);
  }
  addMeta(meta, "结果",
    "成功 " + Number(data.success_count || 0) + " 个栏目 · 失败 " +
    Number(data.failure_count || 0) + " 个栏目");
  meta.hidden = false;
}

function renderTaskPreviewFailures(data) {
  const failed = Array.isArray(data.failed_sections) ? data.failed_sections : [];
  elements.taskPreviewFailed.replaceChildren();
  if (failed.length === 0) {
    elements.taskPreviewFailed.hidden = true;
    return;
  }
  for (const entry of failed) {
    const row = document.createElement("p");
    row.className = "preview-block-error";
    const label = entry && (entry.label || taskItemLabel(entry.content_type));
    const reason = entry && typeof entry.message === "string" && entry.message
      ? entry.message
      : "该栏目未能获取内容。";
    row.textContent = (label || "栏目") + "：" + reason;
    elements.taskPreviewFailed.append(row);
  }
  elements.taskPreviewFailed.hidden = false;
}

function renderTaskPreview(data) {
  if (!data || typeof data !== "object") {
    throw new Error("预览数据格式异常。");
  }
  state.taskPreviewTaskRevision = Number.isInteger(data.task_revision)
    ? data.task_revision
    : null;
  const outputs = data.outputs && typeof data.outputs === "object" ? data.outputs : {};
  state.taskPreviewOutputs = {
    text: typeof outputs.text === "string" ? outputs.text : "",
    markdown: typeof outputs.markdown === "string" ? outputs.markdown : "",
  };
  // 先清掉上一次预览的长图状态，再记录本次快照；顺序反了会把快照 id 清空。
  resetTaskPreviewImages();
  const snapshot = data.image_snapshot && typeof data.image_snapshot === "object"
    ? data.image_snapshot
    : null;
  state.taskPreviewSnapshotId = snapshot && typeof snapshot.id === "string"
    ? snapshot.id
    : null;
  renderTaskPreviewMeta(data);
  const sections = Array.isArray(data.sections) ? data.sections : [];
  elements.taskPreviewSections.replaceChildren();
  for (const section of sections) {
    if (!section || typeof section !== "object") continue;
    elements.taskPreviewSections.append(renderPreviewSection(section));
  }
  renderTaskPreviewFailures(data);
  state.taskPreviewFormat = "text";
  renderPreviewOutput();
  elements.taskPreviewImagePages.replaceChildren();
  syncPreviewImageButton();
  showTaskPreviewStatus(
    sections.length === 0
      ? "预览完成，但没有可展示的栏目内容。"
      : "预览完成：实际使用站点 " + (data.site_name || "未知") + "。预览不会发送消息，也不改动任务配置。",
    "success",
  );
}

async function requestTaskPreview(force) {
  const taskId = state.taskPreviewTaskId;
  if (!bridge || !taskId || state.taskPreviewLoading) {
    return false;
  }
  const requestId = ++state.taskPreviewRequestId;
  // 预览请求的始终是已保存版本；若预览的正是正在编辑的任务，
  // 响应还要能对上发起时的表单签名，避免旧响应落到改过的表单上。
  const formSignature = state.editingTask && state.editingTask.id === taskId
    ? taskFormSignatureKey()
    : null;
  const stillCurrent = () => {
    if (requestId !== state.taskPreviewRequestId || taskId !== state.taskPreviewTaskId) {
      return false;
    }
    if (formSignature === null) return true;
    return Boolean(state.editingTask)
      && state.editingTask.id === taskId
      && taskFormSignatureKey() === formSignature;
  };
  clearTaskPreviewResults();
  setTaskPreviewBusy(true);
  showTaskPreviewStatus("正在获取内容…", "info");
  try {
    // 载荷只带 force_refresh：栏目与候选站点一律用已保存的任务配置。
    const data = await bridge.apiPost(
      "tasks/" + encodeURIComponent(taskId) + "/preview",
      { force_refresh: force === true },
    );
    if (!stillCurrent()) return false;
    renderTaskPreview(data && data.data ? data.data : data);
    return true;
  } catch (error) {
    // 失败响应同样可能过期，不能覆盖当前弹窗的提示。
    if (!stillCurrent()) return false;
    clearTaskPreviewResults();
    showTaskPreviewStatus(taskPreviewErrorText(error), "error");
    return false;
  } finally {
    if (requestId === state.taskPreviewRequestId) {
      setTaskPreviewBusy(false);
    }
  }
}

function openTaskPreview(task) {
  if (!bridge || !task || typeof task.id !== "string" || state.taskPreviewLoading) {
    return;
  }
  state.taskPreviewTaskId = task.id;
  elements.taskPreviewTitle.textContent = "预览内容 · " + task.name;
  clearTaskPreviewPanels();
  openDialog(elements.taskPreviewDialog);
  requestTaskPreview(false);
}

function closeTaskPreview() {
  state.taskPreviewRequestId += 1;
  state.taskPreviewTaskId = null;
  closePreviewImageViewer(true);
  closeDialog(elements.taskPreviewDialog);
  clearTaskPreviewPanels();
}

function previewSavedTask() {
  if (state.taskMode !== "edit" || !state.editingTask) {
    showTaskFormError("请先保存任务后再预览。");
    return;
  }
  const hint = taskFormGateHintText();
  if (hint) {
    // 兜底：按钮正常时已被置灰，这里保证入口语义与共用提示完全一致。
    showTaskFormError(hint);
    updateTaskFormGates();
    return;
  }
  openTaskPreview(state.editingTask);
}

function blockIfPendingWrite() {
  if (state.pendingWrite) {
    showStatus(
      "有站点写操作结果尚未确认。请先重新读取最新列表并关闭确认面板。",
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
    state.deleteTargets = [];
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
  const typedName = elements.name.value.trim();
  if (typedName.length > elements.name.maxLength) {
    showFormError(`站点名称最多 ${elements.name.maxLength} 个字符。`);
    elements.name.focus();
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

function setDeleteMode(mode) {
  state.deleteMode = mode;
  elements.deleteTitle.textContent = mode === "batch" ? "批量删除站点" : "删除站点";
  elements.deleteConfirmButton.textContent = mode === "batch"
    ? "删除选中站点"
    : "删除";
}

function renderDeleteSelection(targets) {
  elements.deleteSelection.replaceChildren();
  if (!targets || targets.length === 0) {
    elements.deleteSelection.hidden = true;
    return;
  }
  for (const site of targets) {
    const item = document.createElement("li");
    item.textContent = site.name + "（" + site.base_url + "）";
    elements.deleteSelection.append(item);
  }
  elements.deleteSelection.hidden = false;
}

function resetDeleteResults() {
  elements.deleteResults.replaceChildren();
  elements.deleteResults.hidden = true;
}

const DELETE_RESULT_TEXT = {
  deleted: "已删除",
  kept: "未删除",
  changed: "未删除：列表已变化或站点已不在",
  unknown: "结果待确认：请刷新列表确认，不会自动重试",
};

function renderDeleteResults(results, total) {
  elements.deleteResults.replaceChildren();
  const deleted = results.filter((item) => item.state === "deleted").length;
  const unknown = results.filter((item) => item.state === "unknown").length;
  const summary = document.createElement("li");
  summary.className = "delete-result-summary";
  if (results.length < total) {
    summary.textContent = "正在删除 " + results.length + "/" + total
      + " 个，已成功 " + deleted + " 个…";
  } else {
    summary.textContent = "共 " + total + " 个：已删除 " + deleted + " 个，未删除 "
      + (total - deleted - unknown) + " 个"
      + (unknown > 0 ? "，结果待确认 " + unknown + " 个。" : "。");
  }
  elements.deleteResults.append(summary);
  for (const item of results) {
    const row = document.createElement("li");
    row.className = "delete-result";
    row.dataset.state = item.state;
    const name = document.createElement("strong");
    name.textContent = item.site.name;
    const text = document.createElement("span");
    const label = DELETE_RESULT_TEXT[item.state] || "未删除";
    text.textContent = item.message ? label + "（" + item.message + "）" : label;
    row.append(name, text);
    elements.deleteResults.append(row);
  }
  elements.deleteResults.hidden = false;
}

function openDelete(site) {
  openDeleteTargets([site], "single");
}

function openBatchDelete() {
  const targets = state.sites.filter(
    (site) => state.siteSelection.has(site.id),
  );
  if (targets.length === 0) {
    return;
  }
  openDeleteTargets(targets, "batch");
}

function openDeleteTargets(targets, mode) {
  if (blockIfPendingWrite()) {
    return;
  }
  if (targets.length === 0) {
    return;
  }
  clearPendingPanels();
  resetDeleteResults();
  setDeleteMode(mode);
  state.deleteTarget = mode === "single" ? targets[0] : null;
  state.deleteTargets = targets.slice();
  elements.deleteError.textContent = "";
  elements.deleteError.hidden = true;
  elements.deleteCancelButton.disabled = false;
  elements.deleteConfirmButton.disabled = false;
  if (mode === "batch") {
    elements.deleteMessage.textContent = "确定删除选中的 " + targets.length
      + " 个站点吗？删除后无法从此页面恢复；仍是任务候选站点的会被保留。";
    renderDeleteSelection(targets);
  } else {
    elements.deleteMessage.textContent = "确定删除“" + targets[0].name
      + "”吗？删除后无法从此页面恢复。";
    renderDeleteSelection([]);
  }
  openDialog(elements.deleteDialog);
  elements.deleteCancelButton.focus();
}

async function requestDeleteSite(site) {
  await bridge.apiPost(
    "sites/" + encodeURIComponent(site.id) + "/delete",
    { expected_revision: site.revision },
  );
}

async function confirmBatchDelete() {
  const targets = state.deleteTargets.slice();
  state.deleting = true;
  resetDeleteResults();
  elements.deleteError.hidden = true;
  elements.deleteCancelButton.disabled = true;
  elements.deleteConfirmButton.disabled = true;
  const results = [];
  try {
    for (let index = 0; index < targets.length; index += 1) {
      const site = targets[index];
      elements.deleteConfirmButton.textContent = "删除中…（"
        + (index + 1) + "/" + targets.length + "）";
      try {
        await requestDeleteSite(site);
        results.push({ site, state: "deleted" });
        state.siteSelection.delete(site.id);
        state.siteTest.delete(site.id);
      } catch (error) {
        const message = friendlyErrorMessage(error);
        const stateName = isConflictMessage(message) || isMissingMessage(message)
          ? "changed"
          : isExplicitWriteRejection(message) ? "kept" : "unknown";
        results.push({
          site,
          state: stateName,
          message: stateName === "unknown" ? writeFailureDetail(error) : message,
        });
      }
      renderDeleteResults(results, targets.length);
    }
    // 列表核对也算“删除中”：核对结束前按钮保持禁用，避免重复提交上一轮目标。
    const reloaded = await loadSites();
    if (reloaded) {
      reconcileDeletedSites(results);
    }
    renderDeleteResults(results, targets.length);
    finishBatchDelete(results, targets, reloaded);
  } finally {
    state.deleting = false;
    elements.deleteCancelButton.disabled = false;
    // 没有待处理项时不留下一个可点的“删除选中站点”。
    elements.deleteConfirmButton.disabled = state.deleteTargets.length === 0;
  }
}

function reconcileDeletedSites(results) {
  for (const item of results) {
    const current = findSite(item.site.id);
    if (item.state === "unknown") {
      if (!current) {
        item.state = "deleted";
        item.message = "";
        state.siteSelection.delete(item.site.id);
        state.siteTest.delete(item.site.id);
      } else if (Number(current.revision) === Number(item.site.revision)) {
        item.state = "kept";
        item.message = "列表确认该站点仍在，未执行删除。";
        item.site = current;
      } else {
        item.state = "changed";
        item.message = "列表中的站点版本已变化，已更新为最新状态，请重新确认后再删除。";
        item.site = current;
      }
    } else if (current && (item.state === "changed" || item.state === "kept")) {
      // 换成最新站点对象，重试时不会再提交旧 revision。
      item.site = current;
    }
  }
}

function finishBatchDelete(results, targets, reloaded) {
  const deleted = results.filter((item) => item.state === "deleted");
  const failed = results.filter((item) => item.state !== "deleted");
  const unconfirmed = results.filter((item) => item.state === "unknown");
  const summary = "已删除 " + deleted.length + "/" + targets.length + " 个站点。";
  if (failed.length === 0) {
    closeDialog(elements.deleteDialog);
    state.deleteTarget = null;
    state.deleteTargets = [];
    showStatus(
      reloaded ? summary : summary + "列表刷新失败，请点击“刷新”重试。",
      reloaded ? "success" : "error",
    );
    return;
  }
  // 结果待确认的站点不放进重试目标，避免对未知结果重复提交。
  state.deleteTargets = failed
    .filter((item) => item.state !== "unknown")
    .map((item) => item.site);
  renderDeleteSelection(state.deleteTargets);
  let detail = summary;
  if (reloaded) {
    const presentIds = new Set(state.sites.map((site) => site.id));
    const present = failed.filter((item) => presentIds.has(item.site.id)).length;
    const missing = failed.length - present;
    if (present > 0) {
      detail += "未删除的 " + present + " 个站点仍保留在列表中";
    }
    if (missing > 0) {
      detail += (present > 0 ? "；" : "") + missing + " 个站点已不在列表中";
    }
    detail += unconfirmed.length > 0
      ? "；" + unconfirmed.length + " 个站点的结果待确认，已移出重试目标。"
      : "，可修正后重试。";
  } else {
    detail += failed.length + " 个站点未删除或结果待确认；列表刷新失败，"
      + "请点击“刷新”确认后再处理，待确认的站点不会自动重试。";
  }
  elements.deleteMessage.textContent = detail;
  showStatus(
    reloaded
      ? summary + failed.length + " 个未删除"
        + (unconfirmed.length > 0 ? "（" + unconfirmed.length + " 个结果待确认）" : "")
        + "，见对话框中的逐站结果。"
      : summary + "列表刷新失败，请点击“刷新”重试。",
    "error",
  );
}

async function confirmDelete() {
  if (state.deleting || state.pendingWrite || state.deleteTargets.length === 0) {
    return;
  }
  if (state.deleteMode === "batch") {
    await confirmBatchDelete();
    return;
  }
  const target = state.deleteTargets[0];
  state.deleting = true;
  elements.deleteCancelButton.disabled = true;
  elements.deleteConfirmButton.disabled = true;
  elements.deleteConfirmButton.textContent = "删除中…";

  try {
    await requestDeleteSite(target);
    closeDialog(elements.deleteDialog);
    state.deleteTarget = null;
    state.deleteTargets = [];
    state.siteSelection.delete(target.id);
    state.siteTest.delete(target.id);
    const reloaded = await loadSites();
    showStatus(
      reloaded
        ? "已删除“" + target.name + "”。"
        : "已删除“" + target.name + "”，但列表刷新失败，请点击“刷新”重试。",
      reloaded ? "success" : "error",
    );
  } catch (error) {
    const message = friendlyErrorMessage(error);
    if (isConflictMessage(message) || isMissingMessage(message)) {
      closeDialog(elements.deleteDialog);
      state.deleteTarget = null;
      state.deleteTargets = [];
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
  invalidateTaskFormRequests();
  updateTaskFormGates();
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

function itemsSummary(items) {
  const list = Array.isArray(items) ? items : [];
  if (list.length === 0) return "无栏目";
  return list
    .map((item) => {
      const label = item.label || taskItemLabel(item.content_type);
      return Number.isInteger(item.limit)
        ? `${label} 最多 ${item.limit} 条`
        : `${label} 不限条数`;
    })
    .join(" · ");
}

function taskItemsSummary(task) {
  return itemsSummary(task && task.items);
}

function taskCandidateText(task) {
  const list = task && Array.isArray(task.sites) ? task.sites : [];
  if (list.length === 0) return "无候选站点";
  return list
    .map((item, index) => {
      const name = item && typeof item.name === "string" && item.name
        ? item.name
        : item && item.exists === false
          ? "已删除的站点"
          : (item && item.id) || "未知站点";
      return `${index + 1}. ${name}${item && item.enabled === false ? "（已停用）" : ""}`;
    })
    .join(" → ");
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
    card.className = "site-card task-card";
    card.dataset.taskId = task.id;
    const head = document.createElement("div");
    head.className = "site-card-head";
    const title = document.createElement("h3");
    title.textContent = task.name;
    // 版本号只用于并发校验，不展示给用户。
    head.append(title);

    const meta = document.createElement("div");
    meta.className = "site-meta";
    const items = document.createElement("span");
    items.className = "task-card-items";
    items.textContent = `栏目：${taskItemsSummary(task)}`;
    const output = document.createElement("span");
    output.textContent = `输出：${task.output_label || task.output_format}`;
    const schedule = document.createElement("span");
    schedule.textContent = scheduleLabel(task);
    const preview = document.createElement("span");
    preview.className = "task-schedule-preview";
    preview.textContent = schedulePreviewText(task);
    const runtime = document.createElement("span");
    if (task.next_run_at) {
      runtime.textContent = `实际下次执行（服务端 UTC）：${task.next_run_at}`;
      runtime.className = "task-schedule-runtime";
    }
    const candidateList = Array.isArray(task.sites) ? task.sites : [];
    const usableCandidates = candidateList.filter(
      (item) => item && item.enabled && item.exists !== false,
    );
    const candidates = document.createElement("span");
    candidates.className = "task-card-sites";
    candidates.textContent = `候选站点：${taskCandidateText(task)}`;
    if (candidateList.length > 0 && usableCandidates.length === 0) {
      // 候选全不可用时执行会改发异常提示，卡片上必须能看出来。
      candidates.textContent += "（候选站点均不可用，执行时将发送异常提示）";
    }
    const umoCount = Array.isArray(task.umos)
      ? task.umos.length
      : (Number.isInteger(task.target_count) ? task.target_count : 0);
    const targets = document.createElement("span");
    targets.textContent = `目标数量：${umoCount} 个 UMO`;
    const disabled = document.createElement("span");
    disabled.className = "task-disabled";
    disabled.textContent = task.enabled
      ? "已启用 · 定时调度"
      : "未启用 · 仅管理员手动测试";
    meta.append(items, output, schedule, preview, runtime, candidates, targets, disabled);

    const actions = document.createElement("div");
    actions.className = "task-card-actions";
    const previewButton = makeButton("预览内容", "button button-secondary",
      () => openTaskPreview(task), `预览推送任务 ${task.name} 的内容`);
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
    const remove = makeButton("删除", "button button-quiet-danger", () =>
      openTaskDelete(task), `删除推送任务 ${task.name}`);
    power.dataset.taskPower = "1";
    power.disabled = state.taskPowerSaving;
    actions.append(previewButton, power, test, edit, remove);
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
    const styles = result && Array.isArray(result.image_styles)
      ? result.image_styles
      : [];
    if (styles.length > 0) {
      state.imageStyles = styles;
      renderTaskStyleOptions();
    }
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
  state.taskLimitErrorShown = false;
}

function showTaskFormError(message) {
  elements.taskFormError.textContent = message;
  elements.taskFormError.hidden = false;
}

function setTaskFormDisabled(disabled) {
  state.taskFormDisabled = disabled;
  for (const control of elements.taskForm.querySelectorAll(
    "input, select, textarea, button",
  )) {
    if (control === elements.taskSaveButton) continue;
    if (disabled) {
      // 各控件自己有禁用条件（首个栏目的上移、已全部加入时的下拉）。
      // 一律置真再一律置假会把这些条件抹掉，所以先记下原值。
      control.dataset.formSuspended = control.disabled ? "1" : "0";
      control.disabled = true;
    } else if (control.dataset.formSuspended !== undefined) {
      control.disabled = control.dataset.formSuspended === "1";
      delete control.dataset.formSuspended;
    }
  }
  elements.taskSaveButton.disabled = disabled;
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
      makeButton("移除", "button button-quiet-danger", () => removeTaskSite(index),
        "移除候选站点"),
    );
    row.append(order, name, actions);
    elements.taskSiteList.append(row);
  });
  taskSiteOptions();
  invalidateTaskFormRequests();
  updateTaskFormGates();
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

/* ---------- 任务表单「未保存修改」：预览/能力测试/保存共用同一份判断 ---------- */

// 条数三态：空=不限，1..TASK_ITEM_LIMIT_MAX=该值，其余一律「非法」。
// 非法值既不归一化成「不限」，也不能和任何已保存状态判等，且不改写用户输入。
// badInput 是 number 框对未完成数字（"1e"、"-"）的真实状态：此时 value 已经是空串，
// 只看文本会误判成「不限」。
function taskItemLimitState(raw, badInput) {
  if (badInput) return { kind: "invalid", limit: null };
  const text = String(raw == null ? "" : raw).trim();
  if (!text) return { kind: "unlimited", limit: null };
  if (!/^[0-9]+$/.test(text)) return { kind: "invalid", limit: null };
  const value = Number(text);
  if (!Number.isInteger(value) || value < 1 || value > TASK_ITEM_LIMIT_MAX) {
    return { kind: "invalid", limit: null };
  }
  return { kind: "limit", limit: value };
}

function taskItemLimitErrorMessage(labels) {
  return labels.join("、") + "的条数需为 1-" + TASK_ITEM_LIMIT_MAX +
    " 的整数，留空表示不限。";
}

// 非法条数同时在输入框上留痕：aria-invalid 给读屏，自定义 validity 让原生校验
// 气泡说中文（浏览器对 badInput 只说「请输入一个数字」，看不出该填什么）。
function syncTaskItemLimitValidity(input, item) {
  const invalid = taskItemLimitState(item.limitText, item.limitBadInput).kind === "invalid";
  // 元素自身处于 badInput 时 value 是空串；重绘出的新元素没有 badInput，但 state 记着它。
  // 两种都算「键入的数字还没成形」，此时写「不限」是骗人的。
  const unsettled = Boolean(item.limitBadInput) || input.validity.badInput;
  input.placeholder = unsettled
    ? "请输入 1-" + TASK_ITEM_LIMIT_MAX + " 的整数"
    : "不限";
  input.setCustomValidity(
    invalid ? taskItemLimitErrorMessage([taskItemLabel(item.content_type)]) : "",
  );
  if (invalid) input.setAttribute("aria-invalid", "true");
  else input.removeAttribute("aria-invalid");
  const row = input.closest(".task-item-row");
  const hint = row ? row.querySelector(".task-item-limit-hint") : null;
  if (hint) {
    hint.textContent = invalid
      ? "请输入 1-" + TASK_ITEM_LIMIT_MAX + " 的整数，留空表示不限"
      : "";
    hint.hidden = !invalid;
  }
}

// 一次编辑：元素自己的值/校验态、state、两个入口和过期错误一起跟着走。
function applyTaskItemLimitEdit(input, index) {
  const item = state.taskItems[index];
  if (!item) return;
  // 不改写用户输入：1.5 / 1e2 / abc 原样留在框里，交给共用判断报「需修正」。
  // number 框里的未完成数字（"1e"、"-"）value 已是空串、badInput 为真，
  // 必须单独记住，否则会退化成「不限」。
  item.limitText = input.value;
  item.limitBadInput = input.validity.badInput;
  syncTaskItemLimitValidity(input, item);
  invalidateTaskFormRequests();
  updateTaskFormGates();
}

function currentTaskItemEntries() {
  return state.taskItems.map((item) => {
    const parsed = taskItemLimitState(item.limitText, item.limitBadInput);
    return { content_type: item.content_type, kind: parsed.kind, limit: parsed.limit };
  });
}

function taskItemEntriesSignature(entries) {
  return (Array.isArray(entries) ? entries : []).map((entry) => {
    // 非法输入用独立标记：既不是「不限」的空串，也不是任何已保存条数。
    const value = entry.kind === "limit"
      ? entry.limit
      : (entry.kind === "unlimited" ? "" : entry.kind);
    return entry.content_type + ":" + value;
  }).join(",");
}

function savedTaskItemEntries(task) {
  return savedTaskItems(task).map((item) => ({
    content_type: item.content_type,
    kind: Number.isInteger(item.limit) ? "limit" : "unlimited",
    limit: item.limit,
  }));
}

// 计划按同一种归一化形状比较：周几排序去重、Cron 空白折叠、间隔起点去掉秒。
function taskScheduleSignature(schedule) {
  if (!schedule || typeof schedule !== "object") return { kind: "none" };
  const kind = String(schedule.kind || "");
  const timezone = String(schedule.timezone == null ? "" : schedule.timezone).trim();
  if (kind === "daily") {
    return { kind, timezone, time: String(schedule.time || "").trim() };
  }
  if (kind === "weekly") {
    const weekdays = Array.isArray(schedule.weekdays)
      ? [...new Set(schedule.weekdays.map(Number).filter(Number.isInteger))]
        .sort((a, b) => a - b)
      : String(schedule.weekdays == null ? "" : schedule.weekdays);
    return { kind, timezone, time: String(schedule.time || "").trim(), weekdays };
  }
  if (kind === "interval") {
    const startAt = String(schedule.start_at == null ? "" : schedule.start_at).trim()
      .replace(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}):00$/, "$1");
    return { kind, timezone, start_at: startAt, interval_minutes: Number(schedule.interval_minutes) };
  }
  if (kind === "cron") {
    return {
      kind, timezone,
      expression: String(schedule.expression == null ? "" : schedule.expression)
        .trim().replace(/\s+/g, " "),
    };
  }
  return { kind };
}

function savedTaskSchedule(task) {
  if (!task) return null;
  return task.schedule || task.schedule_raw || null;
}

function currentTaskScheduleSignature() {
  if (!state.editingTaskScheduleDirty) {
    // 未改计划时表单就是已保存计划：普通编辑不会凭空造出计划改动。
    return taskScheduleSignature(savedTaskSchedule(state.editingTask));
  }
  try {
    return taskScheduleSignature(collectTaskSchedule());
  } catch {
    return { kind: "invalid" };
  }
}

function currentTaskFormSignature() {
  return {
    name: String(elements.taskName.value == null ? "" : elements.taskName.value).trim(),
    items: taskItemEntriesSignature(currentTaskItemEntries()),
    sites: state.taskSites.slice().join(","),
    output_format: String(elements.taskOutputFormat.value || ""),
    image_style: String(elements.taskImageStyle?.value || "default"),
    schedule: currentTaskScheduleSignature(),
    umos: collectTaskUmos().join("\n"),
  };
}

function savedTaskFormSignature(task) {
  const umos = task && Array.isArray(task.umos) ? task.umos : [];
  return {
    name: String(task && task.name != null ? task.name : "").trim(),
    items: taskItemEntriesSignature(savedTaskItemEntries(task)),
    sites: savedTaskSites(task).join(","),
    output_format: String(task && task.output_format != null ? task.output_format : ""),
    image_style: String(task && task.image_style != null ? task.image_style : "default"),
    schedule: taskScheduleSignature(savedTaskSchedule(task)),
    umos: umos.map((umo) => String(umo).trim()).filter((umo) => umo.length > 0).join("\n"),
  };
}

function taskFormSignatureKey() {
  return JSON.stringify(currentTaskFormSignature());
}

function invalidTaskItemLabels() {
  return currentTaskItemEntries()
    .filter((entry) => entry.kind === "invalid")
    .map((entry) => taskItemLabel(entry.content_type));
}

function taskFormMatchesSaved() {
  if (state.taskMode !== "edit" || !state.editingTask) return false;
  return taskFormSignatureKey() ===
    JSON.stringify(savedTaskFormSignature(state.editingTask));
}

// 三处共用这一条提示：条数非法必须修正；其余未保存改动必须先保存。
function taskFormGateHintText() {
  const invalid = invalidTaskItemLabels();
  if (invalid.length > 0) {
    return "栏目条数不合法：" + invalid.join("、") + "。条数需为 1-" +
      TASK_ITEM_LIMIT_MAX + " 的整数，留空表示不限；修正后才能预览或进行能力测试。";
  }
  if (!taskFormMatchesSaved()) {
    return "当前修改尚未保存；预览与能力测试都使用已保存的任务配置，请先保存后再试。";
  }
  return "";
}

function updateTaskFormGates() {
  // 非法行改对或整行删掉后，它留下的红字必须马上收回，不能挂在表单顶部。
  if (state.taskLimitErrorShown && invalidTaskItemLabels().length === 0) {
    clearTaskFormError();
  }
  const blocked = state.taskFormDisabled || state.taskMode !== "edit" ||
    !state.editingTask || state.taskSaving;
  const hint = blocked ? "" : taskFormGateHintText();
  const locked = blocked || hint.length > 0;
  // 忙状态是表单状态之外的第二个维度：请求在途时按钮保持置灰。
  elements.taskCapabilityTestButton.disabled =
    locked || state.taskCapabilityTestInflightId !== 0;
  const preview = elements.taskPreviewFormButton;
  if (preview) {
    preview.disabled = locked;
    // 展开说明：按钮置灰时把原因挂到同一条提示上。
    if (hint) preview.setAttribute("aria-describedby", "task-capability-result");
    else preview.removeAttribute("aria-describedby");
  }
  if (hint) {
    state.taskFormHintText = hint;
    setTaskCapabilityResult(hint, "warning");
  } else if (state.taskFormHintText) {
    // 只收回这里写下的提示，不覆盖能力测试自己的结果。
    state.taskFormHintText = "";
    setTaskCapabilityResult("", null);
  }
}

function updateTaskCapabilityGate() {
  updateTaskFormGates();
}

function updateTaskPreviewGate() {
  updateTaskFormGates();
}

// 表单任一字段变化后，在途的能力测试与预览都作废，避免旧响应落到当前弹窗。
function invalidateTaskFormRequests() {
  state.taskCapabilityTestRequestId += 1;
  // 作废的请求不再持有忙状态：它结束时（成功或失败）不得再动按钮。
  state.taskCapabilityTestInflightId = 0;
  if (state.taskPreviewLoading && state.taskPreviewTaskId === state.editingTaskId) {
    clearTaskPreviewPanels();
    showTaskPreviewStatus("任务表单已修改，本次预览已作废；保存后可重新预览。", "info");
  }
}

async function runTaskCapabilityTest() {
  if (state.taskMode !== "edit" || !state.editingTaskId) {
    setTaskCapabilityResult("请先保存任务后再测试。", "error");
    return;
  }
  const taskId = state.editingTaskId;
  const revision = state.editingTaskRevision;
  const epoch = state.taskFormEpoch;
  const formSignature = taskFormSignatureKey();
  const requestId = ++state.taskCapabilityTestRequestId;
  // 忙状态记在「最新一次请求」的序号上：只有它结束才能解除。
  state.taskCapabilityTestInflightId = requestId;
  elements.taskCapabilityTestButton.disabled = true;
  setTaskCapabilityResult("正在检查候选站点…", "info");
  // 切换任务、关闭弹窗或改动任一表单字段后，旧请求（成功或失败）都作废：
  // 序号认请求世代，epoch 认弹窗世代，签名认表单内容。
  const stillCurrent = () => requestId === state.taskCapabilityTestRequestId
    && epoch === state.taskFormEpoch
    && state.taskMode === "edit"
    && state.editingTaskId === taskId
    && state.editingTaskRevision === revision
    && taskFormSignatureKey() === formSignature;
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
    // 旧请求（成功或失败）既不得解除新请求的忙状态，也不得覆盖它的文案。
    elements.taskDialog.dataset.capabilitySettled = String(requestId);
    if (state.taskCapabilityTestInflightId === requestId) {
      state.taskCapabilityTestInflightId = 0;
      updateTaskCapabilityGate();
    }
  }
}

const DEFAULT_IMAGE_STYLES = [
  { id: "default", name: "默认", description: "白色圆角卡片、莓红页眉强调。" },
];

function taskStyleOptions() {
  return state.imageStyles.length > 0 ? state.imageStyles : DEFAULT_IMAGE_STYLES;
}

function taskStyleName(styleId) {
  const style = taskStyleOptions().find((item) => item.id === styleId);
  return style ? style.name : (styleId || "默认");
}

function renderTaskStyleOptions(selected) {
  if (!elements.taskImageStyle) return;
  const current = selected === undefined
    ? elements.taskImageStyle.value
    : selected;
  const options = taskStyleOptions();
  elements.taskImageStyle.replaceChildren(
    ...options.map((style) => {
      const option = document.createElement("option");
      option.value = style.id;
      option.textContent = style.name;
      if (style.description) option.title = style.description;
      return option;
    }),
  );
  const ids = options.map((style) => style.id);
  elements.taskImageStyle.value = ids.includes(current) ? current : "default";
}

function openCreateTask() {
  if (state.taskSaving || state.taskDeleting || state.taskPending || state.taskPowerPending) {
    return;
  }
  if (blockIfPendingWrite()) {
    return;
  }
  clearTaskPendingPanels();
  // 每次打开表单都是一代新表单：上一代的在途请求全部作废。
  state.taskFormEpoch += 1;
  state.taskFormHintText = "";
  state.taskMode = "create";
  state.editingTask = null;
  state.editingTaskId = null;
  state.editingTaskRevision = null;
  state.taskSites = [];
  elements.taskForm.reset();
  renderTaskStyleOptions("default");
  resetTaskScheduleEditor();
  renderTaskSites();
  setTaskCapabilityResult("", null);
  updateTaskCapabilityGate();
  elements.taskDialogTitle.textContent = "新增推送任务";
  state.taskItems = [];
  renderTaskItems();
  clearTaskFormError();
  setTaskFormDisabled(false);
  elements.taskSaveButton.textContent = "保存任务";
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
  state.taskFormEpoch += 1;
  state.taskFormHintText = "";
  state.taskMode = "edit";
  state.editingTaskId = task.id;
  state.editingTaskRevision = task.revision;
  elements.taskDialogTitle.textContent = "编辑推送任务";
  elements.taskName.value = task.name;
  elements.taskOutputFormat.value = task.output_format;
  renderTaskStyleOptions(task.image_style || "default");
  elements.taskUmos.value = Array.isArray(task.umos) ? task.umos.join("\n") : "";
  state.editingTask = task;
  state.taskItems = savedTaskItems(task).map((item) => ({
    content_type: item.content_type,
    limitText: Number.isInteger(item.limit) ? String(item.limit) : "",
    limitBadInput: false,
  }));
  state.taskSites = Array.isArray(task.sites)
    ? task.sites.map((item) => (typeof item === "string" ? item : item.id))
    : [];
  renderTaskItems();
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
  let items;
  try {
    items = collectTaskItems();
  } catch (error) {
    showTaskFormError(friendlyErrorMessage(error));
    return;
  }
  if (items.length === 0) {
    showTaskFormError("请至少添加一个内容栏目后再保存。");
    elements.taskItemPicker?.focus();
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
    items,
    output_format: elements.taskOutputFormat.value,
    image_style: elements.taskImageStyle?.value || "default",
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
  // 与后端规则保持一致；候选站点此前停用也不再阻止启用。
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
  appendPowerLine("内容栏目", taskItemsSummary(task));
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
    `确定删除“${task.name}”吗？删除任务不会删除它的候选站点。`;
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
    preparing: "准备中", running: "运行中", sending: "发送中", success: "成功",
    partial: "部分成功", failed: "失败", unknown: "结果未知",
    no_targets: "空名单", stopped: "已停止", skipped: "未送达",
  })[status] || status;
}

// 记录里出现的错误码必须能读懂：管理页面不暴露发送器原始异常，
// 因此这一层只给中文说明；未登记的码仍回退显示原码，便于排查。
const DELIVERY_ERROR_LABELS = {
  all_sections_failed: "全部栏目都没有可用内容",
  cancelled: "已取消",
  config_changed: "任务或站点配置已变化",
  content_unavailable: "没有可用内容",
  execution_timeout: "内容准备超时，未开始发送",
  internal_error: "内部错误",
  interrupted: "发送被中断",
  never_started: "服务重启后未开始发送",
  record_after_send_failed: "发送器返回成功，但写入投递记录失败，请核对实际收件",
  section_image_failed: "栏目图片生成失败",
  send_failed: "发送失败",
  send_unknown: "发送结果未知",
  sender_budget_exhausted: "发送预算已耗尽，未调用发送器",
  sender_cancelled: "发送已取消",
  sender_exception: "发送器异常，结果未知",
  sender_invalid_payload: "发送内容不符合要求",
  sender_invalid_result: "发送器返回结果无效",
  sender_not_found: "找不到发送目标",
  sender_not_supported: "发送器不支持该消息",
  sender_route_rejected: "发送通道拒绝了本次投递",
  sender_timeout: "已调用发送器后超时，结果未知",
  skipped_after_send_failed: "因前一条发送失败未尝试",
  skipped_after_send_unknown: "因前一条结果未知未尝试",
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
    ["任务", data.task_name],
    ["输出方式", data.output_label],
    ["配置版本", `任务 ${data.task_revision}`],
    ["内容栏目", itemsSummary(data.items)],
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
    (execution.error_code
      ? `错误码：${deliveryErrorLabel(execution.error_code)}`
      : "无执行级错误");
  elements.deliveryResult.append(title, id, content);
  if (execution.status === "unknown") {
    const note = document.createElement("p");
    note.className = "task-disabled";
    note.textContent = "结果不确定，不会自动重发；请人工确认后再决定是否新建测试。";
    elements.deliveryResult.append(note);
  }
  const failedSections = Array.isArray(execution.failed_sections)
    ? execution.failed_sections
    : [];
  if (failedSections.length > 0) {
    const failed = document.createElement("div");
    failed.className = "task-disabled";
    for (const entry of failedSections) {
      const line = document.createElement("p");
      const label = entry && typeof entry.label === "string" && entry.label
        ? entry.label
        : "栏目";
      const reason = sectionImageErrorLabel(
        entry && typeof entry.reason === "string" ? entry.reason : "",
      );
      line.textContent = reason ? `${label} · ${reason}` : label;
      failed.append(line);
    }
    elements.deliveryResult.append(failed);
  }
  for (const target of execution.targets || []) {
    const row = document.createElement("div");
    row.className = "delivery-target";
    const umo = document.createElement("code");
    umo.textContent = target.umo;
    const detail = document.createElement("span");
    // 目标自己的终态也要能解释：已调用发送器后超时与预算耗尽未调用
    // 在目标行上必须可区分，图片槽位只说明单张图发生了什么。
    const targetCode = deliveryErrorLabel(target.error_code);
    const images = (target.images || []).map((page) => {
      const label = typeof page.label === "string" && page.label
        ? `（${page.label}）`
        : "";
      const code = deliveryErrorLabel(page.error_code);
      return `${deliverySlotLabel(execution, page.image_index)} ` +
        `${deliveryStatusLabel(page.status)}${label}` +
        (code ? `（${code}）` : "");
    }).join("；");
    detail.textContent = `${deliveryStatusLabel(target.status)}` +
      (targetCode ? `（${targetCode}）` : "") +
      ` · ${images}`;
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
    actions.className = "task-card-actions";
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
  items_invalid: "内容栏目无效",
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
  await Promise.all([loadSites(), loadTasks(), loadScheduleRuns(true), loadDeliveries(true)]);
}

function bindEvents() {
  elements.taskSiteAddButton.addEventListener("click", addTaskSite);
  elements.taskCapabilityTestButton.addEventListener(
    "click", runTaskCapabilityTest,
  );
  elements.siteSelectAll.addEventListener("click", selectAllSites);
  elements.siteSelectNone.addEventListener("click", clearSiteSelection);
  elements.siteSelectInvert.addEventListener("click", invertSiteSelection);
  elements.siteTestSelected.addEventListener("click", () => {
    runSiteTests(selectedSiteIds());
  });
  elements.siteDeleteSelected.addEventListener("click", openBatchDelete);
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
  // 条数非法（含 number 框的 badInput）时浏览器会直接拦下 submit，handleTaskSave
  // 根本不会执行；在 invalid 事件上补一条可读的中文错误，不依赖原生气泡。
  elements.taskForm.addEventListener("invalid", () => {
    const invalidItems = invalidTaskItemLabels();
    if (invalidItems.length === 0) return;
    state.taskLimitErrorShown = true;
    showTaskFormError(taskItemLimitErrorMessage(invalidItems));
  }, true);
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
  elements.taskDialog.addEventListener("close", () => {
    // 关闭编辑弹窗即换代：在途的能力测试/预览响应不得再落到任何界面。
    state.taskFormEpoch += 1;
    state.taskCapabilityTestRequestId += 1;
    state.taskCapabilityTestInflightId = 0;
    state.taskFormHintText = "";
    if (state.taskPreviewLoading && state.taskPreviewTaskId === state.editingTaskId) {
      closeTaskPreview();
    }
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
  elements.taskItemAddButton.addEventListener("click", addTaskItem);
  // 名称、输出方式、UMO、图片样式此前没有任何监听：改动它们同样要算
  // 「未保存修改」，并立即作废在途的能力测试与预览。
  [elements.taskName, elements.taskOutputFormat, elements.taskUmos].forEach((field) => {
    if (!field) return;
    field.addEventListener("input", () => {
      invalidateTaskFormRequests();
      updateTaskFormGates();
    });
  });
  if (elements.taskImageStyle) {
    elements.taskImageStyle.addEventListener("change", () => {
      invalidateTaskFormRequests();
      updateTaskFormGates();
    });
  }
  elements.taskPreviewFormButton.addEventListener("click", previewSavedTask);
  elements.taskPreviewRefreshButton.addEventListener(
    "click",
    () => requestTaskPreview(true),
  );
  elements.taskPreviewDoneButton.addEventListener("click", closeTaskPreview);
  elements.taskPreviewCloseButton.addEventListener("click", closeTaskPreview);
  elements.taskPreviewDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeTaskPreview();
  });
  elements.taskPreviewDialog.addEventListener("close", () => {
    state.taskPreviewRequestId += 1;
    state.taskPreviewTaskId = null;
    closePreviewImageViewer(true);
  });
  elements.taskPreviewRoot.addEventListener("change", (event) => {
    if (
      event.target instanceof HTMLInputElement
      && event.target.name === "task-preview-output-format"
    ) {
      state.taskPreviewFormat =
        event.target.value === "markdown" ? "markdown" : "text";
      renderPreviewOutput();
    }
  });
  elements.taskPreviewCopyButton.addEventListener("click", copyPreviewOutput);
  elements.taskPreviewDownloadButton.addEventListener(
    "click",
    downloadPreviewOutput,
  );
  elements.taskPreviewImageButton.addEventListener("click", generatePreviewImage);
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
      state.deleteTargets = [];
      state.deleteMode = "single";
      resetDeleteResults();
      renderDeleteSelection([]);
      refreshSiteToolbar();
    }
  });
}

/* ---------- 视图导航：只切换显示状态，不读取或写入任何业务数据 ---------- */

const NAV_VIEW_DEFINITIONS = [
  { view: "sites", label: "站点管理" },
  { view: "preview", label: "内容预览" },
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
  taskSignature: "",
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
  if (dialog === elements.taskDialog) {
    formGuardState.taskSignature = taskFormSignatureKey();
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
  // 栏目与候选站点的顺序、条数的非法态都不在控件快照里，靠整表签名补上。
  if (dialog === elements.taskDialog) {
    return taskFormSignatureKey() !== formGuardState.taskSignature;
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
  await Promise.all([loadSites(), loadTasks(), loadScheduleRuns(true), loadDeliveries(true)]);
  syncDeliveryPendingPanel();
}

bindNavigation();
init();
