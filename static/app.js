const state = {
  dashboard: null,
  selectedFundCode: null,
  selectedDetail: null,
  aiConfig: null,
  learningTopics: [],
  aiDraft: {
    provider: null,
    modelByProvider: {},
    customByProvider: {},
  },
};

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

function formatMoney(value) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 2,
  }).format(value || 0);
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "--";
  }
  return `${value.toFixed(2)}%`;
}

function formatSyncTime(value) {
  if (!value) {
    return "未同步";
  }
  return value.replace("T", " ");
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderInlineMarkdown(text) {
  let html = escapeHtml(text);
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  html = html.replace(/(^|[\s(])\*([^*]+)\*(?=[\s).,!?:;]|$)/g, '$1<em>$2</em>');
  html = html.replace(/(^|[\s(])_([^_]+)_(?=[\s).,!?:;]|$)/g, '$1<em>$2</em>');
  return html;
}

function renderMarkdown(text) {
  const source = String(text ?? "").replaceAll("\r\n", "\n");
  const lines = source.split("\n");
  const blocks = [];
  const paragraph = [];
  let listType = null;
  let listItems = [];

  function flushParagraph() {
    if (!paragraph.length) {
      return;
    }
    blocks.push(`<p>${paragraph.map((line) => renderInlineMarkdown(line)).join("<br />")}</p>`);
    paragraph.length = 0;
  }

  function flushList() {
    if (!listType || !listItems.length) {
      listType = null;
      listItems = [];
      return;
    }
    blocks.push(`<${listType}>${listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</${listType}>`);
    listType = null;
    listItems = [];
  }

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }

    const headingMatch = line.match(/^(#{1,4})\s+(.*)$/);
    if (headingMatch) {
      flushParagraph();
      flushList();
      const level = headingMatch[1].length;
      blocks.push(`<h${level}>${renderInlineMarkdown(headingMatch[2])}</h${level}>`);
      continue;
    }

    const quoteMatch = line.match(/^>\s?(.*)$/);
    if (quoteMatch) {
      flushParagraph();
      flushList();
      blocks.push(`<blockquote>${renderInlineMarkdown(quoteMatch[1])}</blockquote>`);
      continue;
    }

    const unorderedMatch = line.match(/^[-*]\s+(.*)$/);
    if (unorderedMatch) {
      flushParagraph();
      if (listType && listType !== "ul") {
        flushList();
      }
      listType = "ul";
      listItems.push(unorderedMatch[1]);
      continue;
    }

    const orderedMatch = line.match(/^\d+\.\s+(.*)$/);
    if (orderedMatch) {
      flushParagraph();
      if (listType && listType !== "ol") {
        flushList();
      }
      listType = "ol";
      listItems.push(orderedMatch[1]);
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  flushParagraph();
  flushList();

  return blocks.join("");
}

function scoreClass(tag) {
  return `tag tag-${tag || "neutral"}`;
}

function setMessage(message, isError = false) {
  const target = document.getElementById("import-result");
  target.textContent = message;
  target.className = isError ? "muted danger-text" : "muted success-text";
}

function getProviderMeta(providerId) {
  return state.aiConfig?.available_providers?.find((item) => item.id === providerId) || null;
}

function recommendedModelFor(providerId) {
  const provider = getProviderMeta(providerId);
  return provider?.env_model || provider?.default_model || "";
}

function updateAIModelHint(providerId) {
  const provider = getProviderMeta(providerId);
  const target = document.getElementById("ai-model-hint");
  const recommendedModel = recommendedModelFor(providerId);
  const currentModel = document.getElementById("ai-model-input").value.trim();
  const isCustom =
    state.aiDraft.customByProvider[providerId] ||
    (currentModel && recommendedModel && currentModel !== recommendedModel);

  if (!provider) {
    target.textContent = "";
    return;
  }

  if (isCustom) {
    target.textContent = `推荐模型是 ${recommendedModel}。当前保留的是你为 ${provider.label} 手动填写的模型。`;
    return;
  }

  target.textContent = `推荐模型是 ${recommendedModel}。切换 provider 时会自动带上推荐值。`;
}

function syncModelInput(providerId, { forceDefault = false } = {}) {
  const target = document.getElementById("ai-model-input");
  const recommendedModel = recommendedModelFor(providerId);
  if (!recommendedModel) {
    return;
  }
  const rememberedModel = state.aiDraft.modelByProvider[providerId];
  const rememberedIsCustom = Boolean(state.aiDraft.customByProvider[providerId]);

  target.placeholder = recommendedModel;
  if (forceDefault) {
    target.value = rememberedIsCustom && rememberedModel ? rememberedModel : recommendedModel;
  } else {
    target.value = rememberedModel || recommendedModel;
  }
  updateAIModelHint(providerId);
}

function rememberCurrentModel() {
  const providerId = state.aiDraft.provider;
  if (!providerId) {
    return;
  }
  const currentModel = document.getElementById("ai-model-input").value.trim();
  const recommendedModel = recommendedModelFor(providerId);
  state.aiDraft.modelByProvider[providerId] = currentModel || recommendedModel;
  state.aiDraft.customByProvider[providerId] = Boolean(currentModel) && currentModel !== recommendedModel;
}

function renderAIConfig(config) {
  state.aiConfig = config;
  const selector = document.getElementById("ai-provider-selector");
  selector.innerHTML = (config.available_providers || [])
    .map((provider) => `<option value="${provider.id}">${provider.label}</option>`)
    .join("");
  selector.value = config.provider;

  const modelByProvider = {};
  const customByProvider = {};
  (config.available_providers || []).forEach((provider) => {
    const recommendedModel = provider.env_model || provider.default_model;
    modelByProvider[provider.id] = provider.id === config.provider ? config.model : recommendedModel;
    customByProvider[provider.id] = provider.id === config.provider && config.model !== recommendedModel;
  });
  state.aiDraft = {
    provider: config.provider,
    modelByProvider,
    customByProvider,
  };
  syncModelInput(config.provider, { forceDefault: true });

  const activeProvider = getProviderMeta(config.provider);
  const configuredText = activeProvider?.configured
    ? `已检测到 ${activeProvider.active_key_env}。`
    : `还没有检测到 ${activeProvider?.key_envs?.join(" / ") || "API Key"}。`;
  document.getElementById("ai-config-note").textContent = `当前使用 ${config.provider_label} / ${config.model}，${configuredText}`;
}

function renderSummary(summary) {
  const cards = [
    { label: "持仓基金数", value: `${summary.fund_count || 0} 只`, tone: "gold" },
    { label: "投入本金", value: formatMoney(summary.total_cost), tone: "sky" },
    { label: "当前市值", value: formatMoney(summary.total_value), tone: "mint" },
    {
      label: "累计盈亏",
      value: `${formatMoney(summary.pnl_value)} / ${formatPercent(summary.pnl_ratio)}`,
      tone: summary.pnl_value >= 0 ? "rose" : "slate",
    },
  ];

  document.getElementById("summary-cards").innerHTML = cards
    .map(
      (card) => `
        <article class="summary-card tone-${card.tone}">
          <p>${card.label}</p>
          <h3>${card.value}</h3>
        </article>
      `
    )
    .join("");
}

function renderStrategyFocus(detail) {
  const target = document.getElementById("strategy-focus");
  if (!detail) {
    target.innerHTML = `<div class="empty">先导入或选择一只基金，这里会聚焦展示当前最重要的策略信息。</div>`;
    return;
  }

  const { fund, analysis, latest_report } = detail;
  const holding = analysis.holding;
  const leadingAction = latest_report?.action_plan?.[0] || analysis.reasons[0] || "先观察趋势和仓位，再决定是否调整。";
  const keyRisk = latest_report?.watch_points?.[0] || analysis.reasons[1] || "继续关注波动和回撤。";
  const whyNow = latest_report?.reason_analysis?.[0] || analysis.reasons[0] || "当前还没有足够多的信号。";
  const executionTip = analysis.reasons[2] || latest_report?.action_plan?.[1] || "先管仓位，再看趋势是否延续。";
  const riskHeat = clamp(
    Math.round((Number(analysis.metrics.max_drawdown) * 0.6 + Number(analysis.metrics.volatility) * 0.4) || 0),
    0,
    100
  );
  const trendHeat = clamp(Math.round((Number(analysis.metrics.return_3m) || 0) + 50), 0, 100);
  const confidenceHeat = clamp(Math.round((analysis.confidence || 0) * 100), 0, 100);
  const positionState = holding
    ? `${formatMoney(holding.current_value)} / ${formatPercent(holding.current_return * 100)}`
    : "暂未持有";
  const checklist = [
    `先执行：${analysis.action}`,
    `风险警示：${keyRisk}`,
    `操作提醒：${executionTip}`,
  ];

  target.innerHTML = `
    <article class="cockpit-shell">
      <div class="cockpit-topbar">
        <span class="cockpit-pill">当前主盯 ${escapeHtml(fund.name)}</span>
        <span class="${scoreClass(analysis.tag)}">${escapeHtml(analysis.action)}</span>
      </div>
      <div class="cockpit-layout">
        <section class="command-panel">
          <p class="section-kicker">Command</p>
          <h3>${escapeHtml(analysis.action)}</h3>
          <p class="command-copy">${escapeHtml(leadingAction)}</p>
          <div class="command-subcopy">
            <p><strong>为什么现在这样看：</strong>${escapeHtml(whyNow)}</p>
            <p><strong>你眼下最该防的：</strong>${escapeHtml(keyRisk)}</p>
          </div>
        </section>
        <section class="cockpit-side">
          <div class="gauge-grid">
            <article class="gauge-card">
              <span>信号把握度</span>
              <strong>${confidenceHeat}</strong>
              <div class="meter"><div class="meter-fill tone-gold" style="width:${confidenceHeat}%"></div></div>
            </article>
            <article class="gauge-card">
              <span>趋势热度</span>
              <strong>${trendHeat}</strong>
              <div class="meter"><div class="meter-fill tone-mint" style="width:${trendHeat}%"></div></div>
            </article>
            <article class="gauge-card">
              <span>风险热度</span>
              <strong>${riskHeat}</strong>
              <div class="meter"><div class="meter-fill tone-rose" style="width:${riskHeat}%"></div></div>
            </article>
            <article class="gauge-card">
              <span>当前持仓状态</span>
              <strong>${escapeHtml(positionState)}</strong>
              <p class="cell-sub">策略分 ${escapeHtml(analysis.score)} · 近 3 月 ${formatPercent(analysis.metrics.return_3m)}</p>
            </article>
          </div>
        </section>
      </div>
      <div class="cockpit-strip">
        <div>
          <span>基金风格</span>
          <strong>${escapeHtml(fund.category)}</strong>
        </div>
        <div>
          <span>你当前收益</span>
          <strong>${holding ? formatPercent(holding.current_return * 100) : "未持有"}</strong>
        </div>
        <div>
          <span>波动率</span>
          <strong>${formatPercent(analysis.metrics.volatility)}</strong>
        </div>
        <div>
          <span>最大回撤</span>
          <strong>${formatPercent(analysis.metrics.max_drawdown)}</strong>
        </div>
      </div>
      <div class="checklist-card">
        <span>执行清单</span>
        <ul>
          ${checklist.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
        </ul>
      </div>
    </article>
  `;
}

function renderSignals(signals) {
  const target = document.getElementById("signal-list");
  if (!target) return; // 容错处理
  if (!signals.length) {
    target.innerHTML = `<p class="empty">暂无其他策略信号。</p>`;
    return;
  }
  const selectedCode = state.selectedFundCode;
  const compactSignals = signals.filter((signal) => signal.fund_code !== selectedCode).slice(0, 3);
  const list = compactSignals.length ? compactSignals : signals.slice(0, 3);
  target.innerHTML = list
    .map(
      (signal) => `
        <article class="signal-item" style="margin-top: 12px; padding: 16px; border-radius: 12px; background: #f8fafc; border: 1px solid var(--line);">
          <div class="signal-top" style="display: flex; justify-content: space-between; align-items: center;">
            <strong style="font-size: 15px;">${escapeHtml(signal.fund_name)}</strong>
            <span class="${scoreClass(signal.tag)}">${escapeHtml(signal.action)}</span>
          </div>
          <p class="muted" style="font-size: 13px; margin-top: 8px;">策略分 ${escapeHtml(signal.score)} · ${escapeHtml(signal.reasons[0] || "趋势观察中")}</p>
        </article>
      `
    )
    .join("");
}

function renderPositions(positions) {
  const target = document.getElementById("positions-table");
  if (!positions.length) {
    target.innerHTML = `<div class="empty">还没有持仓，先在上面的导入框里贴你的基金清单。</div>`;
    return;
  }
  target.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>基金</th>
          <th>当前市值</th>
          <th>持有成本</th>
          <th>持有收益</th>
          <th>最新净值</th>
          <th>建议</th>
        </tr>
      </thead>
      <tbody>
        ${positions
          .map(
            (item) => `
              <tr data-code="${item.fund_code}">
                <td>
                  <button class="linkish" data-select-fund="${item.fund_code}">
                    <strong>${item.fund_name}</strong>
                  </button>
                  <p class="cell-sub">${item.category} · ${item.fund_code}</p>
                  <p class="cell-sub">来源 真实数据</p>
                </td>
                <td>
                  ${formatMoney(item.current_value)}
                  <p class="cell-sub">估算份额 ${item.shares}</p>
                </td>
                <td>${formatMoney(item.cost_value)}</td>
                <td class="${item.pnl_value >= 0 ? "rise" : "fall"}">
                  ${formatMoney(item.pnl_value)}<br/>
                  <span class="cell-sub">${formatPercent(item.pnl_ratio)}</span>
                </td>
                <td>${item.current_nav.toFixed(4)}</td>
                <td>
                  <span class="${scoreClass(item.analysis.tag)}">${item.analysis.action}</span>
                  <p class="cell-sub">分数 ${item.analysis.score}</p>
                </td>
              </tr>
            `
          )
          .join("")}
      </tbody>
    </table>
  `;

  target.querySelectorAll("[data-select-fund]").forEach((button) => {
    button.addEventListener("click", () => selectFund(button.dataset.selectFund));
  });
}

function renderWatchlist(funds) {
  const target = document.getElementById("watchlist-grid");
  target.innerHTML = funds
    .map(
      (fund) => `
        <article class="watch-card">
          <div class="watch-top">
            <div>
              <h3>${fund.fund_name}</h3>
              <p class="muted">${fund.category} · ${fund.fund_code}</p>
            </div>
            <span class="${scoreClass(fund.analysis.tag)}">${fund.analysis.action}</span>
          </div>
          <p class="metric-line">3月收益 ${formatPercent(fund.analysis.metrics.return_3m)} · 最大回撤 ${formatPercent(
            fund.analysis.metrics.max_drawdown
          )}</p>
          <p class="muted">${fund.analysis.reasons[0] || "暂无说明。"}</p>
              <button class="secondary small" data-select-fund="${fund.fund_code}">查看详情</button>
              <p class="cell-sub">来源 真实数据 · 同步 ${formatSyncTime(
                fund.last_synced_at
              )}</p>
        </article>
      `
    )
    .join("");

  target.querySelectorAll("[data-select-fund]").forEach((button) => {
    button.addEventListener("click", () => selectFund(button.dataset.selectFund));
  });
}

function buildSparkline(history) {
  if (!history.length) {
    return "";
  }
  const width = 360;
  const height = 140;
  const padding = 10;
  const values = history.map((item) => item.unit_nav);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = history
    .map((item, index) => {
      const x = padding + (index / Math.max(history.length - 1, 1)) * (width - padding * 2);
      const y = height - padding - ((item.unit_nav - min) / span) * (height - padding * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return `
    <svg viewBox="0 0 ${width} ${height}" class="sparkline" aria-label="净值走势">
      <defs>
        <linearGradient id="lineFill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="rgba(246, 180, 34, 0.35)"></stop>
          <stop offset="100%" stop-color="rgba(246, 180, 34, 0.02)"></stop>
        </linearGradient>
      </defs>
      <polyline fill="none" stroke="#f6b422" stroke-width="3" points="${points}"></polyline>
    </svg>
  `;
}

function renderFundDetail(detail) {
  const target = document.getElementById("fund-detail");
  const { fund, analysis, history, latest_report } = detail;
  const holdingText = analysis.holding
    ? `当前市值 ${formatMoney(analysis.holding.current_value)}，持有成本 ${formatMoney(
        analysis.holding.cost_value
      )}，收益率 ${formatPercent(analysis.holding.current_return * 100)}`
    : "当前未持有，可作为观察池候选";
  const quickDigest = latest_report?.summary || analysis.reasons[0] || "当前还没有足够多的说明。";
  const mustKnow = latest_report?.watch_points?.[0] || analysis.reasons[1] || "继续关注趋势和回撤。";

  const reportBlock = latest_report
    ? `
      <section class="report-card">
        <div class="report-head">
          <div>
            <p class="section-kicker">AI Report</p>
            <h3>${escapeHtml(latest_report.title)}</h3>
          </div>
          <p class="cell-sub">${escapeHtml(latest_report.provider_label || latest_report.model_name)} · ${
            escapeHtml(latest_report.provider_model || latest_report.model_name)
          } · ${formatSyncTime(latest_report.generated_at)}</p>
        </div>
        ${
          latest_report.used_fallback && latest_report.fallback_reason
            ? `<p class="fallback-note">当前已回退到规则解读：${escapeHtml(latest_report.fallback_reason)}</p>`
            : ""
        }
        <div class="report-summary-bar">
          <span>先看结论</span>
          <p class="report-summary">${escapeHtml(latest_report.summary)}</p>
        </div>
        <details class="report-layer" open>
          <summary>第一层：为什么最近会涨跌</summary>
          <div class="report-layer-body">
            ${latest_report.reason_analysis.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
          </div>
        </details>
        <details class="report-layer">
          <summary>第二层：上涨和下跌驱动</summary>
          <div class="report-layer-grid">
            <div>
              <h4>上涨驱动</h4>
              ${latest_report.rise_drivers.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
            </div>
            <div>
              <h4>下跌驱动</h4>
              ${latest_report.fall_drivers.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
            </div>
          </div>
        </details>
        <details class="report-layer">
          <summary>第三层：接下来怎么看、怎么做</summary>
          <div class="report-layer-grid">
            <div>
              <h4>接下来要看</h4>
              ${latest_report.watch_points.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
            </div>
            <div>
              <h4>操作建议</h4>
              ${latest_report.action_plan.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
            </div>
          </div>
        </details>
        ${
          latest_report.news_highlights?.length
            ? `<details class="report-layer">
                <summary>第四层：相关新闻线索</summary>
                <div class="news-list report-layer-body">
                  ${latest_report.news_highlights
                    .map(
                      (item) =>
                        `<p><a href="${item.link}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a><span class="cell-sub"> · ${escapeHtml(item.source)}</span></p>`
                    )
                    .join("")}
                </div>
              </details>`
            : ""
        }
      </section>
    `
    : `
      <section class="report-card empty">
        这只基金还没有 AI 解读，点上面的“生成今日 AI 解读”即可生成。
      </section>
    `;

  target.innerHTML = `
    <div class="detail-head">
      <div>
        <h3>${escapeHtml(fund.name)}</h3>
        <p class="muted">${escapeHtml(fund.category)} · 基金经理 ${escapeHtml(fund.manager)}</p>
        <p class="cell-sub">来源 真实数据 · 最近同步 ${formatSyncTime(
          fund.last_synced_at
        )}</p>
      </div>
      <span class="${scoreClass(analysis.tag)}">${escapeHtml(analysis.action)}</span>
    </div>
    <p class="muted">${escapeHtml(fund.description)}</p>
    <div class="detail-overview">
      <div class="detail-chart">
        ${buildSparkline(history)}
        <p class="holding-line">${holdingText}</p>
      </div>
      <div class="detail-side">
        <div class="detail-highlight">
          <span>一句话先看懂</span>
          <strong>${escapeHtml(analysis.action)}</strong>
          <p>${escapeHtml(quickDigest)}</p>
        </div>
        <div class="metric-grid">
          <div><span>最新净值</span><strong>${analysis.metrics.latest_nav.toFixed(4)}</strong></div>
          <div><span>近1月</span><strong>${formatPercent(analysis.metrics.return_1m)}</strong></div>
          <div><span>近3月</span><strong>${formatPercent(analysis.metrics.return_3m)}</strong></div>
          <div><span>近6月</span><strong>${formatPercent(analysis.metrics.return_6m)}</strong></div>
          <div><span>波动率</span><strong>${formatPercent(analysis.metrics.volatility)}</strong></div>
          <div><span>最大回撤</span><strong>${formatPercent(analysis.metrics.max_drawdown)}</strong></div>
        </div>
        <div class="detail-reason-block">
          <span>你现在最该记住</span>
          <p>${escapeHtml(mustKnow)}</p>
        </div>
      </div>
    </div>
    <div class="reason-list detail-reason-block">
      <h4>当前判断依据</h4>
      ${analysis.reasons.map((reason) => `<p>${escapeHtml(reason)}</p>`).join("")}
    </div>
    ${reportBlock}
  `;
}

function renderAssistantSuggestions(detail) {
  const target = document.getElementById("assistant-suggestions");
  if (!detail) {
    target.innerHTML = "";
    return;
  }
  const { fund, analysis } = detail;
  const suggestions = [
    { label: "什么是最大回撤？", mode: "qa" },
    { label: `为什么 ${fund.name} 会提示${analysis.action}？`, mode: "qa" },
    { label: `${fund.name} 现在最大的风险是什么？`, mode: "qa" },
    { label: "请用新手能懂的话解释定投。", mode: "learning" },
  ];
  target.innerHTML = suggestions
    .map(
      (item, index) =>
        `<button class="chip" data-assistant-chip="${index}" data-mode="${item.mode}">${escapeHtml(item.label)}</button>`
    )
    .join("");
  target.querySelectorAll("[data-assistant-chip]").forEach((button, index) => {
    button.addEventListener("click", () => {
      document.getElementById("assistant-question").value = suggestions[index].label;
      askAssistant(suggestions[index].mode, suggestions[index].label);
    });
  });
}

function renderAssistantAnswer(payload) {
  const target = document.getElementById("assistant-answer");
  if (!payload) {
    target.className = "assistant-answer empty";
    target.textContent = "你可以先点一个推荐问题，或者直接输入你不懂的词和疑问。";
    return;
  }

  target.className = "assistant-answer";
  target.innerHTML = `
    <h4>${escapeHtml(payload.title)}</h4>
    <div class="markdown-content">${renderMarkdown(payload.answer)}</div>
    ${
      payload.key_points?.length
        ? `<ul>${payload.key_points.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
        : ""
    }
    ${
      payload.follow_ups?.length
        ? `<div class="chip-row">${payload.follow_ups
            .map(
              (item, index) =>
                `<button class="chip" data-follow-up="${index}">${escapeHtml(item)}</button>`
            )
            .join("")}</div>`
        : ""
    }
    <p class="assistant-meta">
      ${escapeHtml(payload.provider_label || "AI 助手")} · ${escapeHtml(payload.provider_model || "")}
      ${payload.used_fallback && payload.fallback_reason ? ` · 已回退：${escapeHtml(payload.fallback_reason)}` : ""}
    </p>
  `;

  target.querySelectorAll("[data-follow-up]").forEach((button, index) => {
    button.addEventListener("click", () => {
      const question = payload.follow_ups[index];
      document.getElementById("assistant-question").value = question;
      askAssistant("qa", question);
    });
  });
}

function renderLearningTopics(topics) {
  const target = document.getElementById("learning-topics");
  if (!topics.length) {
    target.innerHTML = `<div class="empty">还没有学习主题，先选择一只基金后再来看。</div>`;
    return;
  }
  target.innerHTML = topics
    .map(
      (topic, index) => `
        <article class="learning-topic">
          <span>推荐学习主题 ${index + 1}</span>
          <h4>${escapeHtml(topic.title)}</h4>
          <p>${escapeHtml(topic.summary)}</p>
          <ul>
            <li>${escapeHtml(topic.why_it_matters)}</li>
          </ul>
          <div class="toolbar">
            <button class="secondary small" data-learning-ask="${index}">让 AI 讲明白</button>
          </div>
        </article>
      `
    )
    .join("");

  target.querySelectorAll("[data-learning-ask]").forEach((button, index) => {
    button.addEventListener("click", () => {
      const question = topics[index].suggested_question;
      document.getElementById("assistant-question").value = question;
      askAssistant("learning", question);
    });
  });
}

function renderLearningRoadmap(detail, topics) {
  const target = document.getElementById("learning-roadmap");
  const fundName = detail?.fund?.name || "当前基金";
  const leadTopicId = topics?.[0]?.id || "";
  const activeStepIndex = (() => {
    if (["nav-basics"].includes(leadTopicId)) {
      return 0;
    }
    if (["drawdown-volatility", "high-volatility", "drawdown-survival"].includes(leadTopicId)) {
      return 1;
    }
    if (["index-tracking", "qdii-currency", "index-vs-active"].includes(leadTopicId)) {
      return 2;
    }
    if (["position-sizing", "take-profit", "dca-basics"].includes(leadTopicId)) {
      return 3;
    }
    return 0;
  })();
  const steps = [
    {
      title: "第一步：先看懂页面语言",
      focus: "净值、收益率、持仓成本",
      desc: "先搞清楚自己到底赚了多少、亏了多少，以及基金现在在哪个位置。",
      question: "请像给新手上第一课一样，解释净值、收益率、持仓成本分别是什么意思。",
    },
    {
      title: "第二步：再看懂风险语言",
      focus: "波动率、最大回撤、仓位",
      desc: "只有先知道这只基金跌起来会多疼，你才知道仓位该不该重。",
      question: "波动率和最大回撤有什么区别？我应该先看哪个？",
    },
    {
      title: "第三步：认识基金类型",
      focus: "指数基金、主动基金、QDII",
      desc: `把 ${fundName} 放回它的类别里看，你才知道它为什么会这样涨跌。`,
      question: "指数基金、主动基金、QDII 分别适合什么样的新手？",
    },
    {
      title: "第四步：学会行动策略",
      focus: "定投、止盈、复盘",
      desc: "最后再学什么时候分批买、什么时候先收利润、什么时候先别动。",
      question: "定投、止盈和复盘，作为新手我应该先学哪个？",
    },
  ];

  target.innerHTML = `
    <div class="roadmap-head">
      <p class="section-kicker">Roadmap</p>
      <h3>新手路线图</h3>
      <p class="muted">按从易到难学，先看懂基础语言，再理解风险，最后学会做动作。</p>
    </div>
    <div class="roadmap-steps">
      ${steps
        .map(
          (step, index) => `
            <article class="roadmap-step ${index === activeStepIndex ? "is-active" : ""}">
              <div class="roadmap-step-top">
                <span class="roadmap-index">0${index + 1}</span>
                <div>
                  <h4>${escapeHtml(step.title)}</h4>
                  <p class="muted">${escapeHtml(step.focus)}</p>
                </div>
              </div>
              <p>${escapeHtml(step.desc)}</p>
              <div class="toolbar">
                <button class="secondary small" data-roadmap-ask="${index}">从这一步开始学</button>
              </div>
            </article>
          `
        )
        .join("")}
    </div>
  `;

  target.querySelectorAll("[data-roadmap-ask]").forEach((button, index) => {
    button.addEventListener("click", () => {
      const question = steps[index].question;
      document.getElementById("assistant-question").value = question;
      askAssistant("learning", question);
    });
  });
}

async function loadLearningTopics(fundCode = "") {
  try {
    const suffix = fundCode ? `?fund_code=${encodeURIComponent(fundCode)}` : "";
    const result = await request(`/api/learning/topics${suffix}`);
    state.learningTopics = result.topics || [];
    renderLearningRoadmap(state.selectedDetail, state.learningTopics);
    renderLearningTopics(state.learningTopics);
  } catch (error) {
    document.getElementById("learning-roadmap").innerHTML = `<div class="empty">路线图加载失败：${escapeHtml(
      error.message
    )}</div>`;
    document.getElementById("learning-topics").innerHTML = `<div class="empty">学习区加载失败：${escapeHtml(
      error.message
    )}</div>`;
  }
}

async function askAssistant(mode = "qa", presetQuestion = "") {
  const input = document.getElementById("assistant-question");
  const question = (presetQuestion || input.value).trim();
  if (!question) {
    setMessage("先输入一个你想问的问题。", true);
    return;
  }
  const answerTarget = document.getElementById("assistant-answer");
  answerTarget.className = "assistant-answer";
  answerTarget.innerHTML = `<p>AI 助手正在整理解释，请稍等...</p>`;

  try {
    const result = await request("/api/assistant/ask", {
      method: "POST",
      body: JSON.stringify({
        question,
        fund_code: state.selectedFundCode,
        mode,
      }),
    });
    renderAssistantAnswer(result);
  } catch (error) {
    answerTarget.className = "assistant-answer empty";
    answerTarget.textContent = `AI 助手暂时没答上来：${error.message}`;
  }
}

function fillSelector(funds) {
  const selector = document.getElementById("fund-selector");
  selector.innerHTML = funds
    .map(
      (fund) => `<option value="${fund.fund_code}">${fund.fund_name} · ${fund.fund_code}</option>`
    )
    .join("");
  if (state.selectedFundCode) {
    selector.value = state.selectedFundCode;
  }
  selector.onchange = () => selectFund(selector.value);
}

async function selectFund(code) {
  state.selectedFundCode = code;
  document.getElementById("fund-selector").value = code;
  if (state.dashboard?.top_signals) {
    renderSignals(state.dashboard.top_signals);
  }
  const detail = await request(`/api/funds/${code}`);
  state.selectedDetail = detail;
  renderStrategyFocus(detail);
  renderAssistantSuggestions(detail);
  renderAssistantAnswer(null);
  renderFundDetail(detail);
  await loadLearningTopics(code);
}

async function refreshDashboard(preferredCode = null) {
  const dashboard = await request("/api/dashboard");
  state.dashboard = dashboard;
  document.getElementById("generated-at").textContent = `更新于 ${dashboard.generated_at.replace("T", " ")}`;
  renderSummary(dashboard.summary);
  renderSignals(dashboard.top_signals);
  renderPositions(dashboard.positions);
  renderWatchlist(dashboard.watchlist);
  fillSelector(dashboard.funds);

  const nextCode =
    preferredCode ||
    state.selectedFundCode ||
    dashboard.positions[0]?.fund_code ||
    dashboard.watchlist[0]?.fund_code ||
    dashboard.funds[0]?.fund_code;

  if (nextCode) {
    await selectFund(nextCode);
  } else {
    state.selectedDetail = null;
    renderStrategyFocus(null);
    renderAssistantSuggestions(null);
    renderAssistantAnswer(null);
    await loadLearningTopics();
    document.getElementById("fund-detail").innerHTML = `<div class="empty">还没有可展示的基金详情。</div>`;
  }
}

async function refreshRealData(heldOnly) {
  try {
    const result = await request("/api/funds/refresh", {
      method: "POST",
      body: JSON.stringify({ held_only: heldOnly }),
    });
    const errorText = result.errors?.length ? `，失败 ${result.errors.length} 只` : "";
    setMessage(`已刷新 ${result.refreshed.length} 只基金的真实数据${errorText}`);
    await refreshDashboard();
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function quickImportPosition() {
  const fundCode = document.getElementById("quick-fund-code").value.trim();
  const fundName = document.getElementById("quick-fund-name").value.trim();
  const holdingAmount = document.getElementById("quick-holding-amount").value.trim();
  const returnRate = document.getElementById("quick-return-rate").value.trim();

  try {
    const result = await request("/api/positions/quick-import", {
      method: "POST",
      body: JSON.stringify({
        fund_code: fundCode,
        fund_name: fundName,
        holding_amount: holdingAmount,
        holding_return_rate: returnRate,
        replace: true,
      }),
    });
    setMessage(
      `已导入 ${result.fund_name}，当前市值 ${formatMoney(result.holding_amount)}，收益率 ${formatPercent(
        result.holding_return_rate
      )}`
    );
    await refreshDashboard(result.fund_code);
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function generateTodayReport() {
  const fundCode = state.selectedFundCode;
  if (!fundCode) {
    setMessage("请先选择或导入一只基金。", true);
    return;
  }
  try {
    const result = await request("/api/reports/generate", {
      method: "POST",
      body: JSON.stringify({ fund_code: fundCode, refresh_first: true }),
    });
    const report = result.reports?.[0];
    setMessage(report ? `已生成 ${report.fund_code} 的今日 AI 解读。` : "没有生成新的解读。");
    await refreshDashboard(fundCode);
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function loadAIConfig() {
  try {
    const result = await request("/api/ai/config");
    renderAIConfig(result);
  } catch (error) {
    document.getElementById("ai-config-note").textContent = `AI 配置读取失败：${error.message}`;
  }
}

async function saveAIConfig() {
  const provider = document.getElementById("ai-provider-selector").value;
  const model = document.getElementById("ai-model-input").value.trim() || recommendedModelFor(provider);
  try {
    const result = await request("/api/ai/config", {
      method: "POST",
      body: JSON.stringify({ provider, model }),
    });
    renderAIConfig(result);
    setMessage(`已切换到 ${result.provider_label} / ${result.model}`);
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function testAIConfig() {
  try {
    const result = await request("/api/ai/test", {
      method: "POST",
      body: "{}",
    });
    setMessage(`模型连接正常：${result.provider_label} / ${result.model}`);
  } catch (error) {
    setMessage(`模型连接失败：${error.message}`, true);
  }
}

async function importPositions(replace) {
  const content = document.getElementById("import-content").value;
  try {
    const result = await request("/api/positions/import", {
      method: "POST",
      body: JSON.stringify({ content, replace }),
    });
    const refreshedText = result.refreshed_count ? `，并同步了 ${result.refreshed_count} 只真实基金数据` : "";
    const errorText = result.refresh_errors?.length ? `，${result.refresh_errors.length} 只基金同步失败，未写入本地` : "";
    setMessage(
      `已导入 ${result.imported_count} / ${result.requested_count} 条持仓${refreshedText}${errorText}`
    );
    await refreshDashboard();
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function loadDataSource() {
  try {
    const data = await request("/api/data-source");
    document.getElementById("data-source-note").textContent = `真实数据源：${data.provider_name}`;
  } catch (error) {
    document.getElementById("data-source-note").textContent = "当前未加载到数据源说明。";
  }
}

async function setupActions() {
  document.getElementById("quick-import-btn").addEventListener("click", quickImportPosition);
  document.getElementById("generate-report-btn").addEventListener("click", generateTodayReport);
  document.getElementById("assistant-ask-btn").addEventListener("click", () => askAssistant("qa"));
  document.getElementById("assistant-learn-btn").addEventListener("click", () => askAssistant("learning"));
  document.getElementById("import-btn").addEventListener("click", () => importPositions(true));
  document.getElementById("append-btn").addEventListener("click", () => importPositions(false));
  document.getElementById("refresh-held-btn").addEventListener("click", () => refreshRealData(true));
  document.getElementById("refresh-all-btn").addEventListener("click", () => refreshRealData(false));
  document.getElementById("save-ai-config-btn").addEventListener("click", saveAIConfig);
  document.getElementById("test-ai-config-btn").addEventListener("click", testAIConfig);
  document.getElementById("ai-provider-selector").addEventListener("change", (event) => {
    rememberCurrentModel();
    state.aiDraft.provider = event.target.value;
    syncModelInput(event.target.value, { forceDefault: true });
  });
  document.getElementById("ai-model-input").addEventListener("input", (event) => {
    const providerId = document.getElementById("ai-provider-selector").value;
    const currentModel = event.target.value.trim();
    const recommendedModel = recommendedModelFor(providerId);
    state.aiDraft.provider = providerId;
    state.aiDraft.modelByProvider[providerId] = currentModel || recommendedModel;
    state.aiDraft.customByProvider[providerId] = Boolean(currentModel) && currentModel !== recommendedModel;
    updateAIModelHint(providerId);
  });
  document.getElementById("assistant-question").addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      askAssistant("qa");
    }
  });
  document.getElementById("reset-btn").addEventListener("click", async () => {
    await request("/api/positions/reset", { method: "POST", body: "{}" });
    setMessage("已清空持仓。");
    await refreshDashboard();
  });
}

async function boot() {
  await setupActions();
  await loadDataSource();
  await loadAIConfig();
  await refreshDashboard();
}

boot().catch((error) => {
  document.getElementById("fund-detail").innerHTML = `<div class="empty">页面初始化失败：${error.message}</div>`;
});
