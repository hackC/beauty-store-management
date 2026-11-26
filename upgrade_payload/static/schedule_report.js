/* Print representation built from the schedule records already visible to the user. */
const ScheduleReport = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
  }[char]));
  const minuteLabel = minutes =>
    String(Math.floor(minutes/60)).padStart(2,'0') + ':' + String(minutes%60).padStart(2,'0');
  const hours = minutes => (minutes/60).toFixed(1);
  const percent = (amount,total) => total ? (amount/total*100).toFixed(2) : 0;
  const sum = (items,key) => items.reduce((total,item)=>total+item[key],0);

  function summarize(rows, opening, closing) {
    const groups = new Map();
    const bins = [];
    for (let start=opening; start<closing; start+=120) {
      bins.push({label:minuteLabel(start)+'–'+minuteLabel(Math.min(closing,start+120)), count:0});
    }
    let outside = 0;
    for (const row of rows) {
      if (!groups.has(row.staffId)) groups.set(row.staffId,{name:row.staff, service:0, extra:0, rows:[]});
      const group = groups.get(row.staffId);
      group.service += row.service;
      group.extra += row.extra;
      group.rows.push(row);
      if (row.start >= opening && row.start < closing) bins[Math.floor((row.start-opening)/120)].count++;
      else outside++;
    }
    if (outside) bins.push({label:'营业时段外',count:outside});
    return {workload:[...groups.values()], bins, service:sum(rows,'service'), extra:sum(rows,'extra')};
  }

  function build({date, opening, closing, rows, working, resting, conflicts}) {
    const {workload, bins, service, extra} = summarize(rows,opening,closing);
    const maxWork = Math.max(1,...workload.map(item=>item.service+item.extra));
    const maxCount = Math.max(1,...bins.map(item=>item.count));
    const workBars = workload.map(item=>`<div class="work-row">
      <span>${escape(item.name)}</span>
      <div class="work-track" aria-label="服务 ${hours(item.service)} 小时，等待及延长 ${hours(item.extra)} 小时">
        <span class="service" style="width:${percent(item.service,maxWork)}%"></span>
        <span class="extra" style="width:${percent(item.extra,maxWork)}%"></span>
      </div><span class="number">${hours(item.service+item.extra)} 小时</span>
    </div>`).join('');
    const timeBars = bins.map(item=>`<div class="time-bin">
      <div class="time-track">
        <div class="time-bar" style="height:${percent(item.count,maxCount)}%"><strong>${item.count}</strong></div>
      </div><span>${item.label}</span>
    </div>`).join('');
    const detail = workload.map(group=>{
      const body = group.rows.slice().sort((a,b)=>a.start-b.start).map(row=>`<tr>
        <td>${minuteLabel(row.start)}–${minuteLabel(row.start+row.service+row.extra)}</td>
        <td>${escape(row.customer)}</td>
        <td>${escape(row.project)} <small>（${row.service} 分钟）</small></td></tr>`).join('');
      return `<table class="staff-detail"><thead>
        <tr><th colspan="3"><span class="detail-heading">${escape(group.name)}<span>${group.rows.length} 笔</span></span></th></tr>
        <tr><th scope="col">时间</th><th scope="col">客户</th><th scope="col">项目</th></tr>
        </thead><tbody>${body}</tbody></table>`;
    }).join('');
    const empty = '<p class="empty">当日无预约记录</p>';

    return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>美容师日报_${escape(date)}</title><style>
      @page{size:A4;margin:13mm 12mm}
      *{box-sizing:border-box}
      body{margin:0;color:#202c39;background:#fff;font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif}
      main{max-width:940px;margin:auto;padding:20px}
      h1,h2,p{margin:0}
      h1{font-size:28px} h2{font-size:17px;margin-bottom:12px;border-left:3px solid #547b96;padding-left:8px}
      header{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:6px 16px;border-bottom:1px solid #4a6073;padding-bottom:8px}
      .meta,.legend,footer,small,.empty{color:#485a6a}
      .stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-top:12px;gap:12px 0}
      .stats div{padding:3px 10px 3px 12px;border-inline-start:1px solid #cdd6dd}
      .stats div:first-child{border:0;padding-left:0}
      .stats strong{display:block;font-size:21px;font-variant-numeric:tabular-nums}
      section,footer{padding-top:12px;margin-top:14px;border-top:1px solid #a5b3bf}
      .work-row{display:grid;grid-template-columns:minmax(64px,18%) minmax(65px,1fr) 70px;gap:8px;align-items:center;margin:6px 0}
      .work-row span:first-child,.time-bin span,td{overflow-wrap:anywhere}
      .work-track{display:flex;height:12px;border-bottom:1px solid #8295a3}
      .service,.time-bar{background:#547b96}
      .extra{background:#ccd8e1}
      .legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;margin-top:7px}
      .legend i{display:inline-block;width:12px;height:9px;margin-right:4px;vertical-align:baseline}
      .number{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
      .time-chart{display:grid;grid-template-columns:repeat(auto-fit,minmax(74px,1fr));gap:12px}
      .time-bin{text-align:center;min-width:0;font-size:11px}
      .time-track{height:80px;display:flex;align-items:flex-end;justify-content:center;border-bottom:1px solid #8295a3;padding-top:16px}
      .time-bar{width:68%;position:relative;min-height:0}
      .time-bar strong{position:absolute;bottom:100%;left:0;right:0;color:#202c39}
      table{border-collapse:collapse;width:100%;table-layout:fixed}
      th,td{text-align:left;padding:6px;border-bottom:1px solid #d5dce2;vertical-align:top}
      .detail-heading{display:flex;justify-content:space-between;gap:8px}
      .staff-detail+.staff-detail{margin-top:14px}
      .staff-detail th[colspan]{background:#e8eef5}
      .staff-detail th[scope="col"]:first-child{width:28%} .staff-detail th[scope="col"]:nth-child(2){width:24%}
      th{border-block:1px solid #a5b3bf}
      footer{font-size:12px}
      .toolbar{max-width:940px;margin:12px auto 0;padding:0 20px;display:flex;justify-content:flex-end;gap:8px}
      button{background:#fff;color:#344d61;border:1px solid #a5b3bf;border-radius:4px;padding:7px 12px;cursor:pointer}
      @media(max-width:450px){main{padding:12px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.stats strong{font-size:17px}h1{font-size:25px}}
      @media print{
        *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
        .toolbar{display:none}main{padding:0;max-width:none}
        h2{break-after:avoid}.work-row,.time-bin,tr{break-inside:avoid}
        thead{display:table-header-group}
      }
    </style></head><body>
    <div class="toolbar"><button onclick="window.print()">打印 / 保存为 PDF</button><button onclick="window.close()">关闭</button></div>
    <main><header><h1>美容师日报</h1><div class="meta">${escape(date)}　营业 ${minuteLabel(opening)}–${minuteLabel(closing)}</div></header>
    <div class="stats">
      <div>在岗<strong>${working.length} 人</strong></div>
      <div>预约记录<strong>${rows.length} 笔</strong></div>
      <div>服务<strong>${hours(service)} 小时</strong></div>
      <div>占用<strong>${hours(service+extra)} 小时</strong></div>
    </div>
    <section><h2>人员占用时长</h2>
      ${rows.length ? workBars + '<div class="legend"><span><i class="service"></i>服务时长</span><span><i class="extra"></i>等待及延长</span></div>' : empty}
    </section>
    <section><h2>预约开始时段</h2>${rows.length ? `<div class="time-chart" aria-label="每两小时的预约记录数">${timeBars}</div>` : empty}</section>
    <section><h2 class="detail-heading">预约明细 <span>共 ${rows.length} 笔</span></h2>${detail || empty}</section>
    <footer>在岗人员　${escape(working.join('、')||'无')}${resting.length ? '　｜　休息人员　'+escape(resting.join('、')) : ''}
      ${conflicts ? '　｜　'+conflicts+' 笔预约存在时间冲突' : ''}
    </footer></main></body></html>`;
  }

  return {build, summarize};
})();
