const DRAFT_KEY = "paystub-studio-draft-v2";

const SECTION_CONFIG = {
  earnings: [["label", "Label", "text"], ["rate", "Rate", "number"], ["hours", "Hours", "number"], ["current", "Current", "number"], ["ytd", "YTD", "number"]],
  taxes: [["label", "Label", "text"], ["current", "Current", "number"], ["ytd", "YTD", "number"]],
  deductions: [["label", "Label", "text"], ["current", "Current", "number"], ["ytd", "YTD", "number"]],
  adjustments: [["label", "Label", "text"], ["current", "Current", "number"], ["ytd", "YTD", "number"]],
  other_benefits: [["label", "Label", "text"], ["current", "Current", "number"], ["ytd", "YTD", "number"]],
};

const FIELD_LABELS = {
  company_name: "Company name",
  company_address: "Company address",
  employee_name: "Employee name",
  employee_id: "Employee ID",
  pay_period_start: "Pay period start",
  pay_period_end: "Pay period end",
  pay_date: "Pay date",
};

const IMPORT_ACCEPT = {
  json: ".json,application/json",
  excel: ".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  csv: ".zip,application/zip",
};

const PROFILE_TYPE_LABELS = {
  company: "Company",
  employee: "Employee",
  tax: "Tax defaults",
  deduction: "Deduction defaults",
  assignment: "Assignment",
};

const FILING_STATUS_OPTIONS = ["Single", "Married", "Head of Household"];
const FREQUENCY_OPTIONS = ["weekly", "biweekly", "semimonthly", "monthly"];

const state = {
  emptyPaystub: null,
  samplePaystub: null,
  paystub: null,
  template: "detached_check",
  generationMode: "single",
  generationSequenceType: "pay_frequency",
  generationPayFrequency: "biweekly",
  generationStubCount: 1,
  preview: null,
  previewStale: false,
  working: "",
  assignmentOptions: [],
  storageMode: "filesystem",
  assignmentId: "",
  assignmentYear: new Date().getFullYear(),
  assignmentPeriod: 1,
  assignmentPeriods: [],
  profileSummary: {},
  profileFormats: { export: [], import: [] },
  profileCatalog: {},
  activeProfileType: "company",
  activeProfileId: "",
  profileRecord: null,
  profileEditorText: "",
};

const els = {};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  cache();
  bind();
  try {
    const bootstrap = await api("/api/bootstrap");
    applyBootstrap(bootstrap);
    restoreDraft();
    ensureDefaultSelections();
    renderProfileControls();
    await refreshAssignmentPeriods({ silent: true });
    if (state.profileRecord) {
      renderProfileEditorControls();
    } else if ((state.profileCatalog[state.activeProfileType] || []).length) {
      await loadProfileRecord({ silent: true });
    } else {
      await loadNewProfileRecord({ silent: true });
    }
    renderForm();
    renderPreview();
    setDraftStatus("Workspace ready. Drafts save automatically.");
    await refreshPreview(true);
  } catch (error) {
    showMessage(formatError(error), "error");
    setDraftStatus("Unable to load the local app.");
  }
}

function cache() {
  els.form = document.getElementById("paystub-form");
  els.templateSelect = document.getElementById("template-select");
  els.message = document.getElementById("app-message");
  els.draftStatus = document.getElementById("draft-status");
  els.previewStatus = document.getElementById("preview-status");
  els.previewButton = document.getElementById("preview-button");
  els.generateButton = document.getElementById("generate-button");
  els.loadSampleButton = document.getElementById("load-sample-button");
  els.resetButton = document.getElementById("reset-button");
  els.previewBadge = document.getElementById("preview-badge");
  els.previewEmpty = document.getElementById("preview-empty");
  els.previewContent = document.getElementById("preview-content");
  els.previewSummary = document.getElementById("preview-summary");
  els.previewMeta = document.getElementById("preview-meta");
  els.previewGeneration = document.getElementById("preview-generation");
  els.previewLines = document.getElementById("preview-lines");
  els.previewNotes = document.getElementById("preview-notes");
  els.generationMode = document.getElementById("generation_mode");
  els.generationSequenceType = document.getElementById("generation_sequence_type");
  els.generationPayFrequency = document.getElementById("generation_pay_frequency");
  els.generationStubCount = document.getElementById("generation_stub_count");
  els.generationPlanSummary = document.getElementById("generation-plan-summary");
  els.profileSummary = document.getElementById("profile-summary");
  els.assignmentSelect = document.getElementById("assignment-select");
  els.assignmentYear = document.getElementById("assignment-year");
  els.assignmentPeriod = document.getElementById("assignment-period");
  els.assignmentMeta = document.getElementById("assignment-meta");
  els.loadAssignmentButton = document.getElementById("load-assignment-button");
  els.exportFormat = document.getElementById("export-format");
  els.exportProfilesButton = document.getElementById("export-profiles-button");
  els.importFile = document.getElementById("import-file");
  els.importFileStatus = document.getElementById("import-file-status");
  els.importFormat = document.getElementById("import-format");
  els.importProfilesButton = document.getElementById("import-profiles-button");
  els.profileTypeSelect = document.getElementById("profile-type-select");
  els.profileIdSelect = document.getElementById("profile-id-select");
  els.newProfileButton = document.getElementById("new-profile-button");
  els.loadProfileButton = document.getElementById("load-profile-button");
  els.saveProfileButton = document.getElementById("save-profile-button");
  els.profileEditorStatus = document.getElementById("profile-editor-status");
  els.profileFormShell = document.getElementById("profile-form-shell");
  els.profileJsonEditor = document.getElementById("profile-json-editor");
  els.applyProfileJsonButton = document.getElementById("apply-profile-json-button");
  els.repeaters = {
    earnings: document.getElementById("earnings-list"),
    taxes: document.getElementById("taxes-list"),
    deductions: document.getElementById("deductions-list"),
    adjustments: document.getElementById("adjustments-list"),
    other_benefits: document.getElementById("other_benefits-list"),
  };
}

function bind() {
  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await refreshPreview(false);
  });

  els.form.addEventListener("input", handleInput);
  els.form.addEventListener("change", handleInput);

  document.addEventListener("click", async (event) => {
    const trigger = event.target.closest("[data-action]");
    if (!trigger) return;
    const { action, section, index } = trigger.dataset;
    if (action === "add-row" && section) addRow(section);
    if (action === "remove-row" && section) removeRow(section, Number(index));
  });

  els.templateSelect.addEventListener("change", () => {
    state.template = els.templateSelect.value;
    persistDraft();
  });

  [els.generationMode, els.generationSequenceType, els.generationPayFrequency, els.generationStubCount].forEach((field) => {
    field.addEventListener("change", handleGenerationInput);
    field.addEventListener("input", handleGenerationInput);
  });

  els.assignmentSelect.addEventListener("change", async () => {
    state.assignmentId = els.assignmentSelect.value;
    syncGenerationFrequencyFromAssignment({ force: false });
    persistDraft();
    await refreshAssignmentPeriods({ silent: true });
  });

  els.assignmentYear.addEventListener("change", async () => {
    state.assignmentYear = clampYear(els.assignmentYear.value);
    els.assignmentYear.value = String(state.assignmentYear);
    persistDraft();
    await refreshAssignmentPeriods({ silent: true });
  });

  els.assignmentPeriod.addEventListener("change", () => {
    state.assignmentPeriod = Number(els.assignmentPeriod.value || 1);
    persistDraft();
  });

  els.loadAssignmentButton.addEventListener("click", async () => {
    await loadAssignmentDraft();
  });

  els.exportProfilesButton.addEventListener("click", async () => {
    await exportProfiles();
  });

  els.importProfilesButton.addEventListener("click", async () => {
    await importProfiles();
  });

  els.profileTypeSelect.addEventListener("change", async () => {
    state.activeProfileType = els.profileTypeSelect.value;
    state.activeProfileId = "";
    state.profileRecord = null;
    state.profileEditorText = "";
    renderProfileEditorControls();
    await loadNewProfileRecord({ silent: true });
  });

  els.profileIdSelect.addEventListener("change", () => {
    state.activeProfileId = els.profileIdSelect.value;
    renderProfileEditorControls();
  });

  els.newProfileButton.addEventListener("click", async () => {
    await loadNewProfileRecord();
  });

  els.loadProfileButton.addEventListener("click", async () => {
    await loadProfileRecord();
  });

  els.saveProfileButton.addEventListener("click", async () => {
    await saveProfileRecord();
  });

  els.profileJsonEditor.addEventListener("input", () => {
    state.profileEditorText = els.profileJsonEditor.value;
  });

  els.applyProfileJsonButton.addEventListener("click", () => {
    applyProfileJson();
  });

  els.profileFormShell.addEventListener("input", handleProfileFormInput);
  els.profileFormShell.addEventListener("change", handleProfileFormInput);

  els.profileFormShell.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-profile-action]");
    if (!trigger) return;
    const { profileAction, list, index } = trigger.dataset;
    if (profileAction === "add-item" && list) addProfileListItem(list);
    if (profileAction === "remove-item" && list) removeProfileListItem(list, Number(index));
  });

  els.importFormat.addEventListener("change", () => {
    updateImportControls();
  });

  els.importFile.addEventListener("change", () => {
    const upload = els.importFile.files?.[0];
    if (upload) {
      const inferred = inferImportFormat(upload.name);
      if (inferred) els.importFormat.value = inferred;
    }
    updateImportControls();
  });

  els.loadSampleButton.addEventListener("click", async () => {
    state.paystub = structuredClone(state.samplePaystub);
    state.template = "detached_check";
    resetGenerationPlan();
    state.previewStale = true;
    renderForm();
    persistDraft();
    showMessage("Sample payroll data loaded into the editor.", "success");
    await refreshPreview(true);
  });

  els.resetButton.addEventListener("click", async () => {
    if (!window.confirm("Clear the current draft and reset the editor?")) return;
    localStorage.removeItem(DRAFT_KEY);
    state.paystub = structuredClone(state.emptyPaystub);
    state.template = "detached_check";
    resetGenerationPlan();
    state.preview = null;
    state.previewStale = false;
    ensureDefaultSelections();
    renderProfileControls();
    renderForm();
    renderPreview();
    clearErrors();
    setDraftStatus("Draft cleared.");
    showMessage("Draft reset. You can start blank or load the sample again.", "success");
  });

  els.generateButton.addEventListener("click", async () => {
    await generatePdf();
  });
}

function applyBootstrap(bootstrap, { preserveDraft = false } = {}) {
  state.emptyPaystub = bootstrap.empty_paystub;
  state.samplePaystub = bootstrap.sample_paystub;
  state.profileSummary = bootstrap.profile_summary || {};
  state.assignmentOptions = bootstrap.assignment_options || [];
  state.profileFormats = bootstrap.profile_formats || { export: [], import: [] };
  state.profileCatalog = bootstrap.profile_catalog || {};
  state.storageMode = bootstrap.storage_mode || "filesystem";
  state.template = preserveDraft ? state.template : bootstrap.default_template;
  if (!preserveDraft) {
    applyGenerationPlanDefaults(bootstrap.default_generation_plan || {});
  }
  els.templateSelect.innerHTML = bootstrap.templates
    .map((template) => `<option value="${template.value}">${escapeHtml(template.label)}</option>`)
    .join("");
  els.exportFormat.innerHTML = buildFormatOptions(state.profileFormats.export);
  els.importFormat.innerHTML = buildFormatOptions(state.profileFormats.import);
  els.profileTypeSelect.innerHTML = Object.entries(PROFILE_TYPE_LABELS)
    .map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`)
    .join("");
  updateImportControls();
}

function restoreDraft() {
  const saved = localStorage.getItem(DRAFT_KEY);
  if (!saved) {
    state.paystub = structuredClone(state.samplePaystub);
    return;
  }
  try {
    const parsed = JSON.parse(saved);
    state.paystub = parsed.paystub || structuredClone(state.samplePaystub);
    state.template = parsed.template || state.template;
    state.assignmentId = parsed.assignmentId || state.assignmentId;
    state.assignmentYear = clampYear(parsed.assignmentYear || state.assignmentYear);
    state.assignmentPeriod = Number(parsed.assignmentPeriod || state.assignmentPeriod);
    state.activeProfileType = parsed.activeProfileType || state.activeProfileType;
    state.activeProfileId = parsed.activeProfileId || state.activeProfileId;
    state.profileRecord = parsed.profileRecord || state.profileRecord;
    state.profileEditorText = parsed.profileEditorText || state.profileEditorText;
    state.generationMode = parsed.generationMode || state.generationMode;
    state.generationSequenceType = parsed.generationSequenceType || state.generationSequenceType;
    state.generationPayFrequency = parsed.generationPayFrequency || state.generationPayFrequency;
    state.generationStubCount = Number(parsed.generationStubCount || state.generationStubCount);
    setDraftStatus("Restored your last local draft.");
  } catch {
    state.paystub = structuredClone(state.samplePaystub);
  }
}

function ensureDefaultSelections() {
  if (!state.paystub) state.paystub = structuredClone(state.samplePaystub);
  if (!state.assignmentId && state.assignmentOptions.length) {
    state.assignmentId = state.assignmentOptions[0].value;
  }
  if (!PROFILE_TYPE_LABELS[state.activeProfileType]) {
    state.activeProfileType = "company";
  }
  state.assignmentYear = clampYear(state.assignmentYear);
  if (!state.assignmentPeriod) state.assignmentPeriod = 1;
  if (!FREQUENCY_OPTIONS.includes(state.generationPayFrequency)) {
    state.generationPayFrequency = "biweekly";
  }
  if (!["single", "multiple"].includes(state.generationMode)) {
    state.generationMode = "single";
  }
  if (!["pay_frequency", "weekly"].includes(state.generationSequenceType)) {
    state.generationSequenceType = "pay_frequency";
  }
  if (!Number.isFinite(state.generationStubCount) || state.generationStubCount < 1) {
    state.generationStubCount = 1;
  }
  if (state.generationMode === "single") {
    state.generationStubCount = 1;
  }
}

function applyGenerationPlanDefaults(plan = {}) {
  state.generationMode = plan.mode || "single";
  state.generationSequenceType = plan.sequence_type || "pay_frequency";
  state.generationPayFrequency = plan.pay_frequency || "biweekly";
  state.generationStubCount = Number(plan.stub_count || 1);
}

function resetGenerationPlan() {
  state.generationMode = "single";
  state.generationSequenceType = "pay_frequency";
  state.generationStubCount = 1;
  syncGenerationFrequencyFromAssignment({ force: false });
}

function selectedAssignmentOption() {
  return state.assignmentOptions.find((option) => option.value === state.assignmentId) || null;
}

function syncGenerationFrequencyFromAssignment({ force = false } = {}) {
  const assignment = selectedAssignmentOption();
  if (!assignment?.frequency) return;
  if (force || !FREQUENCY_OPTIONS.includes(state.generationPayFrequency)) {
    state.generationPayFrequency = assignment.frequency;
  }
}

function handleGenerationInput() {
  state.generationMode = els.generationMode.value;
  state.generationSequenceType = els.generationSequenceType.value;
  state.generationPayFrequency = els.generationPayFrequency.value;
  state.generationStubCount = Math.max(1, Math.min(26, Number(els.generationStubCount.value || 1)));
  if (state.generationMode === "single") {
    state.generationStubCount = 1;
  }
  state.previewStale = Boolean(state.preview);
  clearFieldError("generation_stub_count");
  renderForm();
  persistDraft();
  renderPreview();
}

function buildGenerationPlan() {
  return {
    mode: state.generationMode,
    sequence_type: state.generationSequenceType,
    pay_frequency: state.generationSequenceType === "weekly" ? "weekly" : state.generationPayFrequency,
    stub_count: state.generationMode === "multiple" ? state.generationStubCount : 1,
  };
}

function handleInput(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement) || !state.paystub) return;
  const { section, index, prop, type } = target.dataset;
  if (section && index !== undefined && prop) {
    state.paystub[section][Number(index)][prop] = type === "number" ? numberValue(target.value) : target.value;
    clearFieldError(`${section}.${index}.${prop}`);
    clearFieldError(section);
  } else if (target.name) {
    if (target.name === "important_notes" || target.name === "footnotes") {
      state.paystub[target.name] = splitLines(target.value);
    } else {
      state.paystub[target.name] = target.value;
    }
    clearFieldError(target.name);
  }
  state.previewStale = Boolean(state.preview);
  persistDraft();
  renderPreview();
}

function renderProfileControls() {
  els.assignmentSelect.innerHTML = state.assignmentOptions.length
    ? state.assignmentOptions.map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`).join("")
    : `<option value="">No saved assignments</option>`;

  if (!state.assignmentOptions.length) {
    state.assignmentId = "";
  } else if (!state.assignmentOptions.some((option) => option.value === state.assignmentId)) {
    state.assignmentId = state.assignmentOptions[0].value;
  }

  els.assignmentSelect.value = state.assignmentId;
  els.assignmentYear.value = String(state.assignmentYear);
  renderAssignmentPeriods();
  renderProfileSummary();
  updateImportControls();
  renderProfileEditorControls();
}

function renderProfileSummary() {
  const items = [
    ["Storage", state.storageMode === "supabase" ? "Supabase" : "Local files"],
    ["Companies", state.profileSummary.companies || 0],
    ["Employees", state.profileSummary.employees || 0],
    ["Tax defaults", state.profileSummary.tax_defaults || 0],
    ["Deductions", state.profileSummary.deduction_defaults || 0],
    ["Assignments", state.profileSummary.assignments || 0],
  ];
  els.profileSummary.innerHTML = items
    .map(([label, value]) => `<div class="summary-pill"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></div>`)
    .join("");
}

function renderProfileEditorControls() {
  const activeType = PROFILE_TYPE_LABELS[state.activeProfileType] ? state.activeProfileType : "company";
  const ids = state.profileCatalog[activeType] || [];
  state.activeProfileType = activeType;
  if (!state.activeProfileId && ids.length) {
    state.activeProfileId = ids[0];
  }
  if (state.activeProfileId && !ids.includes(state.activeProfileId)) {
    state.activeProfileId = ids[0] || "";
  }

  els.profileTypeSelect.value = state.activeProfileType;
  els.profileIdSelect.innerHTML = ids.length
    ? ids.map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`).join("")
    : `<option value="">No saved records</option>`;
  els.profileIdSelect.value = state.activeProfileId;
  els.loadProfileButton.disabled = !state.activeProfileId;
  els.profileEditorStatus.textContent = state.activeProfileId
    ? `Editing ${PROFILE_TYPE_LABELS[state.activeProfileType].toLowerCase()} record ${state.activeProfileId}.`
    : `Create or load a ${PROFILE_TYPE_LABELS[state.activeProfileType].toLowerCase()} record.`;
  els.profileJsonEditor.value = state.profileEditorText;
  renderProfileEditorForm();
}

function renderProfileEditorForm() {
  if (!state.profileRecord) {
    els.profileFormShell.innerHTML = `<div class="profile-list-empty">Load or create a profile record to start editing.</div>`;
    return;
  }

  const type = state.activeProfileType;
  if (type === "company") {
    els.profileFormShell.innerHTML = `
      <section class="profile-form-card">
        <div class="profile-form-head"><h5>Company record</h5></div>
        <div class="profile-form-grid">
          ${profileScalarField("profile_id", "Profile ID")}
          ${profileScalarField("default_payroll_check_number", "Default check number")}
          ${profileScalarField("company_name", "Company name", { wide: true })}
          ${profileScalarField("company_address", "Company address", { as: "textarea", wide: true })}
        </div>
      </section>
    `;
    return;
  }

  if (type === "employee") {
    els.profileFormShell.innerHTML = `
      <section class="profile-form-card">
        <div class="profile-form-head"><h5>Employee identity</h5></div>
        <div class="profile-form-grid">
          ${profileScalarField("profile_id", "Profile ID")}
          ${profileScalarField("employee_id", "Employee ID")}
          ${profileScalarField("employee_name", "Employee name", { wide: true })}
          ${profileScalarField("social_security_number", "Social Security number")}
          ${profileScalarField("employee_address", "Employee address", { as: "textarea", wide: true })}
        </div>
      </section>
      ${renderProfileObjectList("earnings", "Earnings", [["label", "Label", "text"], ["rate", "Rate", "number"], ["hours", "Hours", "number"], ["flat_amount", "Flat amount", "number"]], "Add earning")}
      ${renderProfileObjectList("other_benefits", "Benefits", [["label", "Label", "text"], ["current", "Current", "number"], ["ytd", "YTD", "number"]], "Add benefit")}
      ${renderProfileStringList("important_notes", "Important notes", "Add note")}
    `;
    return;
  }

  if (type === "tax") {
    els.profileFormShell.innerHTML = `
      <section class="profile-form-card">
        <div class="profile-form-head"><h5>Tax defaults</h5></div>
        <div class="profile-form-grid is-three">
          ${profileScalarField("profile_id", "Profile ID")}
          ${profileScalarField("filing_status", "Filing status", { as: "select", options: FILING_STATUS_OPTIONS })}
          ${profileScalarField("frequency", "Pay frequency", { as: "select", options: FREQUENCY_OPTIONS })}
          ${profileScalarField("allowances", "Allowances", { type: "number" })}
          ${profileScalarField("additional_federal_wh", "Additional federal WH", { type: "number" })}
          ${profileScalarField("state", "State")}
          ${profileScalarField("state_tax_rate_override", "State tax override", { type: "number" })}
          ${profileScalarField("local_tax_rate", "Local tax rate", { type: "number" })}
          ${profileScalarField("local_tax_label", "Local tax label")}
        </div>
      </section>
    `;
    return;
  }

  if (type === "deduction") {
    els.profileFormShell.innerHTML = `
      <section class="profile-form-card">
        <div class="profile-form-head"><h5>Deduction defaults</h5></div>
        <div class="profile-form-grid">
          ${profileScalarField("profile_id", "Profile ID")}
        </div>
      </section>
      ${renderProfileObjectList("pre_tax_deductions", "Pre-tax deductions", [["label", "Label", "text"], ["amount", "Amount", "number"]], "Add pre-tax deduction")}
      ${renderProfileObjectList("post_tax_deductions", "Post-tax deductions", [["label", "Label", "text"], ["amount", "Amount", "number"]], "Add post-tax deduction")}
    `;
    return;
  }

  els.profileFormShell.innerHTML = `
    <section class="profile-form-card">
      <div class="profile-form-head"><h5>Assignment mapping</h5></div>
      <div class="profile-form-grid">
        ${profileScalarField("profile_id", "Profile ID")}
        ${profileScalarField("payroll_check_number_start", "Starting check number", { type: "number" })}
        ${profileScalarField("company_profile_id", "Company profile", { as: "select", options: state.profileCatalog.company || [] })}
        ${profileScalarField("employee_profile_id", "Employee profile", { as: "select", options: state.profileCatalog.employee || [] })}
        ${profileScalarField("tax_profile_id", "Tax defaults profile", { as: "select", options: state.profileCatalog.tax || [] })}
        ${profileScalarField("deduction_profile_id", "Deduction defaults profile", { as: "select", options: state.profileCatalog.deduction || [] })}
      </div>
    </section>
  `;
}

function profileScalarField(path, label, options = {}) {
  const { as = "input", type = "text", options: selectOptions = [], wide = false } = options;
  const value = getProfileValue(path);
  const className = wide ? ' class="field" style="grid-column: 1 / -1;"' : ' class="field"';
  if (as === "textarea") {
    return `<label${className}><span>${escapeHtml(label)}</span><textarea data-profile-field="${escapeHtml(path)}" rows="4">${escapeHtml(String(value ?? ""))}</textarea></label>`;
  }
  if (as === "select") {
    return `
      <label${className}>
        <span>${escapeHtml(label)}</span>
        <select data-profile-field="${escapeHtml(path)}">
          ${selectOptions.map((option) => `<option value="${escapeHtml(String(option))}" ${String(value ?? "") === String(option) ? "selected" : ""}>${escapeHtml(String(option))}</option>`).join("")}
        </select>
      </label>
    `;
  }
  return `<label${className}><span>${escapeHtml(label)}</span><input type="${type === "number" ? "number" : "text"}" ${type === "number" ? 'inputmode="decimal" step="0.01"' : ""} data-profile-field="${escapeHtml(path)}" value="${escapeHtml(String(value ?? ""))}" /></label>`;
}

function renderProfileObjectList(listName, title, fields, addLabel) {
  const items = state.profileRecord[listName] || [];
  return `
    <section class="profile-form-card">
      <div class="profile-form-head">
        <h6>${escapeHtml(title)}</h6>
        <button type="button" class="ghost-button" data-profile-action="add-item" data-list="${listName}">${escapeHtml(addLabel)}</button>
      </div>
      <div class="profile-list">
        ${items.length ? items.map((item, index) => `
          <div class="line-item">
            <div class="line-item-head">
              <span class="line-item-title">${escapeHtml(title)} ${index + 1}</span>
              <button type="button" class="ghost-button" data-profile-action="remove-item" data-list="${listName}" data-index="${index}">Remove</button>
            </div>
            <div class="line-item-grid${fields.length <= 3 ? " is-compact" : ""}">
              ${fields.map(([key, fieldLabel, kind]) => `
                <label class="field field-inline">
                  <span>${escapeHtml(fieldLabel)}</span>
                  <input
                    type="${kind === "number" ? "number" : "text"}"
                    ${kind === "number" ? 'inputmode="decimal" step="0.01"' : ""}
                    data-profile-list="${listName}"
                    data-profile-index="${index}"
                    data-profile-prop="${key}"
                    data-profile-kind="${kind}"
                    value="${escapeHtml(String(item[key] ?? ""))}"
                  />
                </label>
              `).join("")}
            </div>
          </div>
        `).join("") : `<div class="profile-list-empty">No entries yet.</div>`}
      </div>
    </section>
  `;
}

function renderProfileStringList(listName, title, addLabel) {
  const items = state.profileRecord[listName] || [];
  return `
    <section class="profile-form-card">
      <div class="profile-form-head">
        <h6>${escapeHtml(title)}</h6>
        <button type="button" class="ghost-button" data-profile-action="add-item" data-list="${listName}">${escapeHtml(addLabel)}</button>
      </div>
      <div class="profile-list">
        ${items.length ? items.map((item, index) => `
          <div class="line-item">
            <div class="line-item-head">
              <span class="line-item-title">${escapeHtml(title)} ${index + 1}</span>
              <button type="button" class="ghost-button" data-profile-action="remove-item" data-list="${listName}" data-index="${index}">Remove</button>
            </div>
            <label class="field">
              <span>Note</span>
              <textarea rows="3" data-profile-list="${listName}" data-profile-index="${index}" data-profile-string="true">${escapeHtml(String(item ?? ""))}</textarea>
            </label>
          </div>
        `).join("") : `<div class="profile-list-empty">No entries yet.</div>`}
      </div>
    </section>
  `;
}

function getProfileValue(path) {
  return state.profileRecord ? state.profileRecord[path] : "";
}

function handleProfileFormInput(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement) || !state.profileRecord) return;

  const field = target.dataset.profileField;
  if (field) {
    state.profileRecord[field] = normalizeProfileValue(target.value, target);
    syncProfileEditorText();
    return;
  }

  const listName = target.dataset.profileList;
  const index = target.dataset.profileIndex;
  if (listName && index !== undefined) {
    const numericIndex = Number(index);
    if (target.dataset.profileString === "true") {
      state.profileRecord[listName][numericIndex] = target.value;
    } else {
      const prop = target.dataset.profileProp;
      state.profileRecord[listName][numericIndex][prop] = normalizeProfileValue(target.value, target);
    }
    syncProfileEditorText();
  }
}

function normalizeProfileValue(value, target) {
  const isNumeric = target instanceof HTMLInputElement && target.type === "number";
  if (!isNumeric) return value;
  if (value === "") return "";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : value;
}

function addProfileListItem(listName) {
  if (!state.profileRecord) return;
  const map = {
    earnings: { label: "", rate: 0, hours: 0, flat_amount: 0 },
    other_benefits: { label: "", current: 0, ytd: 0 },
    pre_tax_deductions: { label: "", amount: 0 },
    post_tax_deductions: { label: "", amount: 0 },
    important_notes: "",
  };
  const nextItem = structuredClone(map[listName] ?? {});
  state.profileRecord[listName] = [...(state.profileRecord[listName] || []), nextItem];
  syncProfileEditorText();
  renderProfileEditorForm();
}

function removeProfileListItem(listName, index) {
  if (!state.profileRecord || !Array.isArray(state.profileRecord[listName])) return;
  state.profileRecord[listName].splice(index, 1);
  syncProfileEditorText();
  renderProfileEditorForm();
}

function applyProfileJson() {
  let parsed;
  try {
    parsed = JSON.parse(els.profileJsonEditor.value || "{}");
  } catch (error) {
    showMessage(`Profile JSON is invalid: ${formatError(error)}`, "error");
    els.profileJsonEditor.focus();
    return false;
  }
  setProfileRecord(parsed);
  showMessage("Advanced JSON applied to the typed editor.", "success");
  return true;
}

function setProfileRecord(record) {
  state.profileRecord = structuredClone(record);
  syncProfileEditorText();
  renderProfileEditorControls();
}

function syncProfileEditorText() {
  state.profileEditorText = JSON.stringify(state.profileRecord ?? {}, null, 2);
  if (els.profileJsonEditor.value !== state.profileEditorText) {
    els.profileJsonEditor.value = state.profileEditorText;
  }
}

async function refreshAssignmentPeriods({ silent = false } = {}) {
  if (!state.assignmentId) {
    state.assignmentPeriods = [];
    renderProfileControls();
    els.assignmentMeta.textContent = "Add assignment profiles to use this loader.";
    return;
  }
  try {
    const response = await api(`/api/assignments/${encodeURIComponent(state.assignmentId)}/periods?year=${state.assignmentYear}`);
    state.assignmentPeriods = response.periods || [];
    if (!state.assignmentPeriods.some((period) => period.number === state.assignmentPeriod)) {
      state.assignmentPeriod = state.assignmentPeriods[0]?.number || 1;
    }
    renderAssignmentPeriods();
    const selectedOption = state.assignmentOptions.find((option) => option.value === state.assignmentId);
    const selectedPeriod = state.assignmentPeriods.find((period) => period.number === state.assignmentPeriod);
    if (selectedOption && selectedPeriod) {
      els.assignmentMeta.textContent =
        `${selectedOption.employee_name} · ${selectedOption.company_name} · ${response.frequency} · ` +
        `Pay date ${selectedPeriod.pay_date} · Check ${selectedPeriod.check_number}`;
    }
    if (!silent) showMessage("Pay periods refreshed from the saved assignment.", "success");
    persistDraft();
  } catch (error) {
    state.assignmentPeriods = [];
    renderAssignmentPeriods();
    els.assignmentMeta.textContent = "Unable to read pay periods for the selected assignment.";
    if (!silent) showMessage(formatError(error), "error");
  }
}

function renderAssignmentPeriods() {
  els.assignmentPeriod.innerHTML = state.assignmentPeriods.length
    ? state.assignmentPeriods
        .map(
          (period) =>
            `<option value="${period.number}">Period ${period.number} · ${escapeHtml(period.start)} to ${escapeHtml(period.end)} · ${escapeHtml(period.pay_date)}</option>`
        )
        .join("")
    : `<option value="">No periods available</option>`;
  if (state.assignmentPeriods.length) {
    els.assignmentPeriod.value = String(state.assignmentPeriod);
  }
  const disabled = !state.assignmentId || !state.assignmentPeriods.length;
  els.assignmentPeriod.disabled = disabled;
  els.loadAssignmentButton.disabled = disabled;
}

function renderForm() {
  [
    "company_name",
    "company_address",
    "employee_name",
    "employee_address",
    "employee_id",
    "pay_date",
    "pay_period_start",
    "pay_period_end",
    "social_security_number",
    "taxable_marital_status",
    "exemptions_allowances",
    "payroll_check_number",
  ].forEach((name) => {
    const field = document.getElementById(name);
    if (field) field.value = state.paystub[name] || "";
  });
  document.getElementById("important_notes").value = (state.paystub.important_notes || []).join("\n");
  document.getElementById("footnotes").value = (state.paystub.footnotes || []).join("\n");
  els.templateSelect.value = state.template;
  els.generationMode.value = state.generationMode;
  els.generationSequenceType.value = state.generationSequenceType;
  els.generationPayFrequency.value = state.generationSequenceType === "weekly" ? "weekly" : state.generationPayFrequency;
  els.generationPayFrequency.disabled = state.generationMode === "single" || state.generationSequenceType === "weekly";
  els.generationStubCount.value = String(state.generationMode === "multiple" ? state.generationStubCount : 1);
  els.generationStubCount.disabled = state.generationMode === "single";
  els.generateButton.textContent = state.generationMode === "multiple" ? "Generate Batch ZIP" : "Generate PDF";
  renderGenerationPlanSummary();

  Object.entries(SECTION_CONFIG).forEach(([section, fields]) => {
    const container = els.repeaters[section];
    const rows = state.paystub[section] || [];
    if (!rows.length) {
      container.innerHTML = `<div class="repeater-empty">${container.dataset.emptyLabel}</div>`;
      return;
    }
    const compact = fields.length === 3 ? " is-compact" : "";
    container.innerHTML = rows
      .map(
        (row, index) => `
          <div class="line-item">
            <div class="line-item-head">
              <span class="line-item-title">${section.replaceAll("_", " ")} ${index + 1}</span>
              <button type="button" class="ghost-button" data-action="remove-row" data-section="${section}" data-index="${index}">Remove</button>
            </div>
            <div class="line-item-grid${compact}">
              ${fields
                .map(([key, label, type]) => {
                  const fieldKey = `${section}.${index}.${key}`;
                  const inputId = `${section}-${index}-${key}`;
                  return `
                    <label class="field field-inline">
                      <span>${label}</span>
                      <input
                        id="${inputId}"
                        type="${type === "number" ? "number" : "text"}"
                        data-section="${section}"
                        data-index="${index}"
                        data-prop="${key}"
                        data-type="${type}"
                        aria-describedby="${inputId}-error"
                        ${type === "number" ? 'inputmode="decimal" step="0.01" min="0"' : ""}
                        value="${escapeHtml(String(row[key] ?? ""))}"
                      />
                      <small id="${inputId}-error" class="field-error row-error" data-for="${fieldKey}"></small>
                    </label>
                  `;
                })
                .join("")}
            </div>
          </div>
        `
      )
      .join("");
  });
  clearErrors();
}

function renderGenerationPlanSummary() {
  const plan = state.preview?.generation_plan || null;
  const isMultiple = state.generationMode === "multiple";
  if (!isMultiple) {
    els.generationPlanSummary.classList.add("is-hidden");
    els.generationPlanSummary.innerHTML = "";
    return;
  }

  const sequenceLabel = state.generationSequenceType === "weekly" ? "Weekly sequence generation" : "Custom pay periods";
  const frequencyLabel = state.generationSequenceType === "weekly"
    ? "Weekly"
    : state.generationPayFrequency.replace("semi", "semi-").replace(/\b\w/g, (char) => char.toUpperCase());

  const rows = (plan?.entries || [])
    .slice(0, 6)
    .map(
      (entry) => `
        <div class="generation-summary-row">
          <span>Stub ${entry.sequence_number} · ${escapeHtml(entry.pay_period_start)} to ${escapeHtml(entry.pay_period_end)}</span>
          <strong>${escapeHtml(entry.pay_date)}</strong>
        </div>
      `
    )
    .join("");

  const helperCopy = plan
    ? `Previewing ${plan.stub_count} scheduled ${plan.stub_count === 1 ? "stub" : "stubs"} from ${escapeHtml(plan.summary.first_pay_date)} through ${escapeHtml(plan.summary.last_pay_date)}.`
    : "Refresh preview to validate the planned pay dates, pay periods, and YTD roll-forward before generating the batch ZIP.";

  els.generationPlanSummary.classList.remove("is-hidden");
  els.generationPlanSummary.innerHTML = `
    <div class="generation-summary-card">
      <strong>${escapeHtml(sequenceLabel)}</strong>
      <p class="field-hint">${escapeHtml(helperCopy)}</p>
      <div class="generation-summary-list">
        <div class="generation-summary-row">
          <span>Frequency</span>
          <strong>${escapeHtml(frequencyLabel)}</strong>
        </div>
        <div class="generation-summary-row">
          <span>Count</span>
          <strong>${escapeHtml(String(state.generationStubCount))}</strong>
        </div>
        ${rows || `<div class="generation-summary-row"><span>Schedule</span><strong>Preview needed</strong></div>`}
      </div>
    </div>
  `;
}

function addRow(section) {
  const row = section === "earnings" ? { label: "", rate: 0, hours: 0, current: 0, ytd: 0 } : { label: "", current: 0, ytd: 0 };
  state.paystub[section].push(row);
  state.previewStale = Boolean(state.preview);
  renderForm();
  persistDraft();
  renderPreview();
}

function removeRow(section, index) {
  state.paystub[section].splice(index, 1);
  state.previewStale = Boolean(state.preview);
  renderForm();
  persistDraft();
  renderPreview();
}

async function loadAssignmentDraft() {
  if (!state.assignmentId || !state.assignmentPeriods.length) {
    showMessage("Select a saved assignment and pay period first.", "error");
    return;
  }
  setWorking("assignment");
  try {
    const response = await api("/api/profiles/load-assignment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        assignment_id: state.assignmentId,
        year: state.assignmentYear,
        period_number: state.assignmentPeriod,
      }),
    });
    state.paystub = response.paystub;
    state.preview = response.preview;
    if (response.generation_plan) {
      state.preview.generation_plan = response.generation_plan;
    }
    syncGenerationFrequencyFromAssignment({ force: true });
    state.previewStale = false;
    renderForm();
    renderPreview();
    persistDraft();
    showMessage(
      `Loaded ${escapeHtml(state.assignmentId)} for period ${escapeHtml(String(response.period.number))}.`,
      "success"
    );
  } catch (error) {
    showMessage(formatError(error), "error");
  } finally {
    clearWorking();
  }
}

async function refreshPreview(silentMessage) {
  const errors = validate();
  if (Object.keys(errors).length) {
    renderErrors(errors);
    showMessage("Complete the required fields before refreshing the preview.", "error");
    return;
  }
  clearErrors();
  setWorking("preview");
  try {
    const response = await api("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paystub: buildSubmissionPaystub(), generation_plan: buildGenerationPlan() }),
    });
    state.preview = response;
    state.paystub = response.paystub;
    state.previewStale = false;
    renderForm();
    renderPreview();
    persistDraft();
    if (!silentMessage) {
      showMessage(
        state.generationMode === "multiple"
          ? "Preview refreshed. The schedule and YTD roll-forward are aligned with the backend plan."
          : "Preview refreshed. Totals are now aligned with the backend model.",
        "success"
      );
    }
  } catch (error) {
    showMessage(formatError(error), "error");
  } finally {
    clearWorking();
  }
}

async function generatePdf() {
  const errors = validate();
  if (Object.keys(errors).length) {
    renderErrors(errors);
    showMessage("Complete the required fields before generating the output.", "error");
    return;
  }
  clearErrors();
  setWorking("generate");
  try {
    const response = await api("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template: state.template, paystub: buildSubmissionPaystub(), generation_plan: buildGenerationPlan() }),
    });
    state.preview = response.preview;
    state.paystub = response.preview.paystub;
    state.previewStale = false;
    renderForm();
    renderPreview();
    persistDraft();
    if (response.mode === "multiple") {
      showMessage(
        `Batch generated successfully. <a href="${response.download_url}">Download the ZIP</a>.`,
        "success"
      );
    } else {
      showMessage(
        `PDF generated successfully. <a href="${response.download_url}">Download the PDF</a>.`,
        "success"
      );
    }
  } catch (error) {
    showMessage(formatError(error), "error");
  } finally {
    clearWorking();
  }
}

async function exportProfiles() {
  setWorking("export");
  try {
    const response = await api("/api/profiles/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_format: els.exportFormat.value }),
    });
    const link = document.createElement("a");
    link.href = response.download_url;
    link.download = response.filename;
    document.body.append(link);
    link.click();
    link.remove();
    showMessage(
      `Profile export ready. <a href="${response.download_url}">Download ${escapeHtml(response.filename)}</a>.`,
      "success"
    );
  } catch (error) {
    showMessage(formatError(error), "error");
  } finally {
    clearWorking();
  }
}

async function importProfiles() {
  const upload = els.importFile.files?.[0];
  if (!upload) {
    showMessage("Choose a profile bundle before importing.", "error");
    return;
  }

  setWorking("import");
  try {
    const formData = new FormData();
    formData.append("file_format", els.importFormat.value);
    formData.append("upload", upload);
    const response = await api("/api/profiles/import", {
      method: "POST",
      body: formData,
    });
    state.profileSummary = response.summary || {};
    state.profileCatalog = response.profile_catalog || state.profileCatalog;
    state.assignmentOptions = response.assignment_options || [];
    ensureDefaultSelections();
    renderProfileControls();
    await refreshAssignmentPeriods({ silent: true });
    els.importFile.value = "";
    updateImportControls();
    persistDraft();
    showMessage("Profiles imported successfully. Saved assignments are available immediately.", "success");
  } catch (error) {
    showMessage(formatError(error), "error");
  } finally {
    clearWorking();
  }
}

async function loadNewProfileRecord({ silent = false } = {}) {
  setWorking("profile");
  try {
    const response = await api(`/api/profiles/${encodeURIComponent(state.activeProfileType)}/_new`);
    state.activeProfileId = "";
    setProfileRecord(response.record);
    persistDraft();
    if (!silent) showMessage(`Started a new ${PROFILE_TYPE_LABELS[state.activeProfileType].toLowerCase()} record.`, "success");
  } catch (error) {
    showMessage(formatError(error), "error");
  } finally {
    clearWorking();
  }
}

async function loadProfileRecord({ silent = false } = {}) {
  if (!state.activeProfileId) {
    showMessage("Choose a saved profile record to load.", "error");
    return;
  }
  setWorking("profile");
  try {
    const response = await api(`/api/profiles/${encodeURIComponent(state.activeProfileType)}/${encodeURIComponent(state.activeProfileId)}`);
    setProfileRecord(response.record);
    persistDraft();
    if (!silent) showMessage(`Loaded profile record ${escapeHtml(state.activeProfileId)}.`, "success");
  } catch (error) {
    showMessage(formatError(error), "error");
  } finally {
    clearWorking();
  }
}

async function saveProfileRecord() {
  let record = state.profileRecord;
  if ((els.profileJsonEditor.value || "").trim() !== state.profileEditorText.trim()) {
    const applied = applyProfileJson();
    if (!applied) return;
    record = state.profileRecord;
  }

  setWorking("profile");
  try {
    const response = await api(`/api/profiles/${encodeURIComponent(state.activeProfileType)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ record }),
    });
    state.profileCatalog = response.profile_catalog || state.profileCatalog;
    state.profileSummary = response.profile_summary || state.profileSummary;
    state.assignmentOptions = response.assignment_options || state.assignmentOptions;
    state.activeProfileId = response.record.profile_id || state.activeProfileId;
    setProfileRecord(response.record);
    renderProfileControls();
    await refreshAssignmentPeriods({ silent: true });
    persistDraft();
    showMessage(`Saved profile record ${escapeHtml(state.activeProfileId)}.`, "success");
  } catch (error) {
    showMessage(formatError(error), "error");
  } finally {
    clearWorking();
  }
}

function validate() {
  const errors = {};
  Object.entries(FIELD_LABELS).forEach(([field, label]) => {
    if (!String(state.paystub[field] || "").trim()) errors[field] = `${label} is required.`;
  });

  Object.entries(SECTION_CONFIG).forEach(([section, fields]) => {
    (state.paystub[section] || []).forEach((row, index) => {
      if (isBlankRow(section, row)) return;
      const label = String(row.label || "").trim();
      if (!label) {
        errors[`${section}.${index}.label`] = "Add a label or remove this row.";
      }
      fields.forEach(([key, , type]) => {
        if (type !== "number") return;
        const value = Number(row[key] || 0);
        if (value < 0) {
          errors[`${section}.${index}.${key}`] = "Use zero or a positive number.";
        }
      });
    });
  });

  const earnings = (state.paystub.earnings || []).filter((row) => !isBlankRow("earnings", row));
  if (!earnings.length || !earnings.some((item) => String(item.label || "").trim())) {
    errors.earnings = "Add at least one earning line with a label.";
  }

  if (state.generationMode === "multiple") {
    if (!Number.isFinite(state.generationStubCount) || state.generationStubCount < 2 || state.generationStubCount > 26) {
      errors.generation_stub_count = "Use a value between 2 and 26 for multiple paystubs.";
    }
  }

  return errors;
}

function renderErrors(errors) {
  clearErrors();
  let firstInvalid = null;
  Object.entries(errors).forEach(([field, message]) => {
    const error = document.querySelector(`.field-error[data-for="${field}"]`);
    const input = getFieldNode(field);
    if (error) error.textContent = message;
    if (input) {
      input.setAttribute("aria-invalid", "true");
      if (!firstInvalid) firstInvalid = input;
    }
  });
  if (firstInvalid && typeof firstInvalid.focus === "function") {
    firstInvalid.focus();
  }
}

function clearErrors() {
  document.querySelectorAll(".field-error").forEach((node) => (node.textContent = ""));
  document.querySelectorAll("[aria-invalid='true']").forEach((node) => node.removeAttribute("aria-invalid"));
}

function clearFieldError(fieldKey) {
  if (!fieldKey) return;
  const error = document.querySelector(`.field-error[data-for="${fieldKey}"]`);
  if (error) error.textContent = "";
  const input = getFieldNode(fieldKey);
  if (input) input.removeAttribute("aria-invalid");
}

function getFieldNode(fieldKey) {
  if (fieldKey.includes(".")) {
    const [section, index, prop] = fieldKey.split(".");
    return document.querySelector(`[data-section="${section}"][data-index="${index}"][data-prop="${prop}"]`);
  }
  return document.getElementById(fieldKey);
}

function renderPreview() {
  if (!state.preview) {
    els.previewEmpty.hidden = false;
    els.previewContent.hidden = true;
    els.previewBadge.className = "preview-badge";
    els.previewBadge.textContent = state.previewStale ? "Preview out of date" : "Awaiting preview";
    els.previewStatus.textContent = "Preview has not been generated yet.";
    return;
  }

  const paystub = state.preview.paystub;
  const summary = state.preview.summary;
  const generationPlan = state.preview.generation_plan || null;
  els.previewEmpty.hidden = true;
  els.previewContent.hidden = false;
  els.previewBadge.className = `preview-badge ${state.previewStale ? "is-stale" : "is-fresh"}`;
  els.previewBadge.textContent = state.previewStale ? "Preview out of date" : "Preview current";
  els.previewStatus.textContent = state.previewStale ? "You changed the form after the last preview." : "Preview reflects the current form values.";

  els.previewSummary.innerHTML = [
    card("Gross pay", currency(summary.gross_pay_current)),
    card("Taxes", currency(summary.total_taxes_current)),
    card("Deductions", currency(summary.total_deductions_current)),
    card("Net pay", currency(summary.net_pay_current)),
  ].join("");

  els.previewMeta.innerHTML = `
    <div class="meta-grid">
      ${meta("Employee", escapeHtml(paystub.employee_name || "Not provided"))}
      ${meta("Employee ID", escapeHtml(paystub.employee_id || "Not provided"))}
      ${meta("Pay date", escapeHtml(paystub.pay_date || "Not provided"))}
      ${meta("Pay period", escapeHtml(`${paystub.pay_period_start || "—"} to ${paystub.pay_period_end || "—"}`))}
      ${meta("YTD gross", currency(summary.gross_pay_ytd))}
      ${meta("YTD net", currency(summary.net_pay_ytd))}
    </div>
  `;

  els.previewGeneration.innerHTML = generationPlan && generationPlan.mode === "multiple"
    ? `
      <div class="notes-card">
        <span class="notes-heading">Generation Flow</span>
        <p>${escapeHtml(
          `${generationPlan.stub_count} stubs scheduled from ${generationPlan.summary.first_pay_date} through ${generationPlan.summary.last_pay_date}.`
        )}</p>
        <ul>
          ${generationPlan.entries
            .slice(0, 6)
            .map(
              (entry) =>
                `<li>${escapeHtml(
                  `Stub ${entry.sequence_number}: ${entry.pay_period_start} to ${entry.pay_period_end}, pay date ${entry.pay_date}, YTD net ${currency(entry.net_pay_ytd)}`
                )}</li>`
            )
            .join("")}
        </ul>
      </div>
    `
    : `
      <div class="notes-card">
        <span class="notes-heading">Generation Flow</span>
        <p>Single paystub mode is active. Switch to multiple paystubs to preview a weekly or pay-frequency sequence before generating.</p>
      </div>
    `;

  els.previewLines.innerHTML = [
    table("Earnings", ["Description", "Rate", "Hours", "Current", "YTD"], paystub.earnings.map((item) => [item.label, num(item.rate), num(item.hours), currency(item.current), currency(item.ytd)])),
    table("Taxes", ["Description", "Current", "YTD"], paystub.taxes.map((item) => [item.label, currency(item.current), currency(item.ytd)])),
    table("Deductions", ["Description", "Current", "YTD"], [...paystub.deductions, ...paystub.adjustments].map((item) => [item.label, currency(item.current), currency(item.ytd)])),
    table("Benefits", ["Description", "Current", "YTD"], paystub.other_benefits.map((item) => [item.label, num(item.current), num(item.ytd)])),
  ].join("");

  const notes = [...(paystub.important_notes || []), ...(paystub.footnotes || [])];
  els.previewNotes.innerHTML = notes.length
    ? `<div class="notes-card"><span class="notes-heading">Notes</span><ul>${notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul></div>`
    : `<div class="notes-card"><span class="notes-heading">Notes</span><p>No notes added to this draft.</p></div>`;
}

function buildSubmissionPaystub() {
  const paystub = structuredClone(state.paystub);
  Object.keys(SECTION_CONFIG).forEach((section) => {
    paystub[section] = (paystub[section] || []).filter((row) => !isBlankRow(section, row));
  });
  return paystub;
}

function isBlankRow(section, row) {
  return SECTION_CONFIG[section].every(([key, , type]) => {
    if (type === "text") return !String(row[key] || "").trim();
    return Number(row[key] || 0) === 0;
  });
}

function persistDraft() {
  localStorage.setItem(
    DRAFT_KEY,
    JSON.stringify({
      template: state.template,
      paystub: state.paystub,
      assignmentId: state.assignmentId,
      assignmentYear: state.assignmentYear,
      assignmentPeriod: state.assignmentPeriod,
      activeProfileType: state.activeProfileType,
      activeProfileId: state.activeProfileId,
      profileRecord: state.profileRecord,
      profileEditorText: state.profileEditorText,
      generationMode: state.generationMode,
      generationSequenceType: state.generationSequenceType,
      generationPayFrequency: state.generationPayFrequency,
      generationStubCount: state.generationStubCount,
    })
  );
  setDraftStatus("Draft saved locally.");
}

function setDraftStatus(message) {
  els.draftStatus.textContent = message;
}

function setWorking(mode) {
  state.working = mode;
  const allButtons = [
    els.previewButton,
    els.generateButton,
    els.loadSampleButton,
    els.resetButton,
    els.loadAssignmentButton,
    els.exportProfilesButton,
    els.importProfilesButton,
    els.newProfileButton,
    els.loadProfileButton,
    els.saveProfileButton,
    els.applyProfileJsonButton,
  ];
  allButtons.forEach((button) => {
    if (button) button.disabled = true;
  });
  els.assignmentSelect.disabled = true;
  els.assignmentYear.disabled = true;
  els.assignmentPeriod.disabled = true;
  els.generationMode.disabled = true;
  els.generationSequenceType.disabled = true;
  els.generationPayFrequency.disabled = true;
  els.generationStubCount.disabled = true;
  els.exportFormat.disabled = true;
  els.importFormat.disabled = true;
  els.importFile.disabled = true;
  els.profileTypeSelect.disabled = true;
  els.profileIdSelect.disabled = true;
  els.profileJsonEditor.disabled = true;
  els.applyProfileJsonButton.disabled = true;

  els.previewButton.textContent = mode === "preview" ? "Refreshing…" : "Refresh preview";
  els.generateButton.textContent = mode === "generate"
    ? (state.generationMode === "multiple" ? "Generating ZIP…" : "Generating…")
    : (state.generationMode === "multiple" ? "Generate Batch ZIP" : "Generate PDF");
  els.loadAssignmentButton.textContent = mode === "assignment" ? "Loading…" : "Load assignment";
  els.exportProfilesButton.textContent = mode === "export" ? "Preparing…" : "Export profiles";
  els.importProfilesButton.textContent = mode === "import" ? "Importing…" : "Import profiles";
  els.newProfileButton.textContent = mode === "profile" ? "Preparing…" : "New profile";
  els.loadProfileButton.textContent = mode === "profile" ? "Loading…" : "Load record";
  els.saveProfileButton.textContent = mode === "profile" ? "Saving…" : "Save profile";
  if (mode === "import") {
    els.importFileStatus.textContent = "Importing the selected profile bundle…";
  }
}

function clearWorking() {
  state.working = "";
  [
    els.previewButton,
    els.generateButton,
    els.loadSampleButton,
    els.resetButton,
    els.exportProfilesButton,
    els.importProfilesButton,
    els.newProfileButton,
    els.loadProfileButton,
    els.saveProfileButton,
    els.applyProfileJsonButton,
  ].forEach((button) => {
    if (button) button.disabled = false;
  });
  els.assignmentSelect.disabled = false;
  els.assignmentYear.disabled = false;
  els.generationMode.disabled = false;
  els.generationSequenceType.disabled = false;
  els.generationPayFrequency.disabled = state.generationMode === "single" || state.generationSequenceType === "weekly";
  els.generationStubCount.disabled = state.generationMode === "single";
  els.exportFormat.disabled = false;
  els.importFormat.disabled = false;
  els.importFile.disabled = false;
  els.profileTypeSelect.disabled = false;
  els.profileIdSelect.disabled = false;
  els.profileJsonEditor.disabled = false;
  els.applyProfileJsonButton.disabled = false;
  renderAssignmentPeriods();
  updateImportControls();
  renderProfileEditorControls();

  els.previewButton.textContent = "Refresh preview";
  els.generateButton.textContent = state.generationMode === "multiple" ? "Generate Batch ZIP" : "Generate PDF";
  els.loadAssignmentButton.textContent = "Load assignment";
  els.exportProfilesButton.textContent = "Export profiles";
  els.importProfilesButton.textContent = "Import profiles";
  els.newProfileButton.textContent = "New profile";
  els.loadProfileButton.textContent = "Load record";
  els.saveProfileButton.textContent = "Save profile";
}

function showMessage(message, tone) {
  els.message.innerHTML = message;
  els.message.className = `message-banner is-visible ${tone === "error" ? "is-error" : "is-success"}`;
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    throw new Error(payload?.detail?.map ? payload.detail.map((item) => `${item.loc.slice(1).join(" → ")}: ${item.msg}`).join(" | ") : payload?.detail || `Request failed with status ${response.status}.`);
  }
  return response.json();
}

function card(label, value) {
  return `<div class="summary-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function meta(label, value) {
  return `<div class="meta-card"><span>${escapeHtml(label)}</span><strong>${value}</strong></div>`;
}

function table(title, headers, rows) {
  if (!rows.length) return `<div class="preview-table"><span class="table-caption">${escapeHtml(title)}</span><p>No entries.</p></div>`;
  return `
    <div class="preview-table">
      <span class="table-caption">${escapeHtml(title)}</span>
      <table>
        <thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(String(cell))}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>
    </div>
  `;
}

function buildFormatOptions(formats) {
  return (formats || []).map((format) => `<option value="${escapeHtml(format)}">${escapeHtml(format.toUpperCase())}</option>`).join("");
}

function inferImportFormat(filename) {
  const value = String(filename || "").toLowerCase();
  if (value.endsWith(".json")) return "json";
  if (value.endsWith(".xlsx") || value.endsWith(".xlsm")) return "excel";
  if (value.endsWith(".zip")) return "csv";
  return "";
}

function updateImportControls() {
  const selectedFormat = els.importFormat.value || "json";
  const hasFile = Boolean(els.importFile.files?.length);
  els.importFile.accept = IMPORT_ACCEPT[selectedFormat] || ".json,.xlsx,.zip";
  els.importProfilesButton.disabled = state.working === "import" ? true : !hasFile;
  if (!hasFile) {
    els.importFileStatus.textContent = "Choose a bundle to enable import.";
    return;
  }
  const upload = els.importFile.files[0];
  const inferred = inferImportFormat(upload.name);
  if (inferred && inferred !== selectedFormat) {
    els.importFileStatus.textContent = `The selected file looks like ${inferred.toUpperCase()}. The format was adjusted automatically.`;
    els.importFormat.value = inferred;
    els.importFile.accept = IMPORT_ACCEPT[inferred];
    return;
  }
  els.importFileStatus.textContent = `Ready to import ${upload.name}.`;
}

function splitLines(value) {
  return value.split("\n").map((line) => line.trim()).filter(Boolean);
}

function numberValue(value) {
  if (value === "" || value === null || value === undefined) return 0;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function clampYear(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return new Date().getFullYear();
  return Math.min(2100, Math.max(2020, Math.trunc(parsed)));
}

function currency(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 }).format(Number(value || 0));
}

function num(value) {
  return Number(value) ? new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value)) : "—";
}

function formatError(error) {
  return error instanceof Error ? error.message : "Unexpected error.";
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
