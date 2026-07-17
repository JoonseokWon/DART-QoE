import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("사용법: node export_workbook.mjs 입력.json 출력.xlsx [미리보기-폴더]");
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const wb = Workbook.create();
wb.comments.setSelf({ displayName: "사용자" });
const cover = wb.worksheets.add("표지");
const inputs = wb.worksheets.add("원천 자료");
const summary = wb.worksheets.add("QoE 요약");
const normalized = wb.worksheets.add("정상화 손익");
const wc = wb.worksheets.add("운전자본");
const netDebt = wb.worksheets.add("순차입금");
const candidates = wb.worksheets.add("검토 후보");
const audit = wb.worksheets.add("검토 흔적");
const checks = wb.worksheets.add("검증");

const navy = "#112A46", blue = "#2F5597", pale = "#DDEBF7", green = "#E2F0D9", amber = "#FFF2CC", red = "#FCE4D6", grey = "#F2F2F2";
const amountFmt = '#,##0;[Red](#,##0);-';
const pctFmt = '0.0%;[Red](0.0%);-';
const daysFmt = '0.0"일";[Red](0.0"일");-';
const multipleFmt = '0.0x;[Red](0.0x);-';
function title(sheet, text, endCol="H") {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${endCol}2`).merge();
  sheet.getRange("A1").values = [[text]];
  sheet.getRange(`A1:${endCol}2`).format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 18 }, verticalAlignment: "center" };
}
function header(range) { range.format = { fill: blue, font: { bold: true, color: "#FFFFFF" }, verticalAlignment: "center", wrapText: true, borders: { preset: "inside", style: "thin", color: "#D9E2F3" } }; }
function widths(sheet, specs) { for (const [col, width] of Object.entries(specs)) sheet.getRange(`${col}:${col}`).format.columnWidth = width; }
function excelCol(index) {
  let value = index;
  let result = "";
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
}

title(cover, "DART-QoE | 정상화 이익과 운전자본 검토", "J");
cover.getRange("B5").format.numberFormat = "000000";
cover.getRange("B6").format.numberFormat = "00000000";
cover.getRange("A4:B11").values = [
  ["회사", data.metadata.company_name], ["종목코드", data.metadata.stock_code], ["DART 고유번호", data.metadata.corp_code],
  ["분석기간", `${data.years[0]}–${data.years.at(-1)}`], ["기준", data.metadata.basis], ["단위", "원"],
  ["리스부채", data.metadata.include_lease ? "순차입금에 포함" : "순차입금에서 제외"], ["작성일", data.metadata.created],
];
cover.getRange("A4:A11").format = { fill: pale, font: { bold: true } };
cover.getRange("D4:J4").merge(); cover.getRange("D4").values = [["목적과 한계"]]; header(cover.getRange("D4:J4"));
cover.getRange("D5:J9").merge(); cover.getRange("D5").values = [["공시상 이익 증가가 지속 가능한 영업성과인지 확인하기 위해 정상화 조정 후보, 운전자본과 현금전환을 함께 검토합니다. 자동으로 기업가치나 정상화 이익을 확정하지 않으며, 거래 검토자가 원문을 확인할 항목과 계산 근거를 빠르게 찾도록 설계한 개념검증 도구입니다."]];
cover.getRange("D5:J9").format = { wrapText: true, verticalAlignment: "top", fill: grey };
cover.getRange("D11:J11").merge(); cover.getRange("D11").values = [["색상: 파랑=사용자 입력 · 초록=시트 간 연결 · 검정=계산식 · 노랑=확인 필요"]];
cover.getRange("D11:J11").format = { fill: amber, font: { italic: true } };
cover.getRange("A14:J14").merge(); cover.getRange("A14").values = [["사용 순서"]]; header(cover.getRange("A14:J14"));
cover.getRange("A15:J18").merge(); cover.getRange("A15").values = [["1) QoE 요약에서 보고이익·정상화 손익·현금흐름 추이를 확인합니다.  2) 검토 후보에서 원문을 확인하고 조정 여부·방향·적용 금액·사유를 입력합니다.  3) 정상화 손익에서 일회성 손익과 사용자 조정 후 영업이익을 확인합니다.  4) 운전자본과 순차입금에서 현금창출력과 자금 구성을 봅니다.  5) 검토 흔적과 검증에서 출처·산식·오류를 확인합니다."]];
cover.getRange("A15:J18").format = { wrapText: true, verticalAlignment: "top" };
widths(cover, {A:20,B:22,C:3,D:17,E:17,F:17,G:17,H:17,I:17,J:17});

title(inputs, "원천 자료 | 공시 추출값", "O");
const sourceHeaders = ["연도","매출액","영업이익","순이익","영업현금흐름","매출채권","재고자산","매입채무","현금및현금성자산","매출원가","차입금·사채","리스부채","추출 기준","자동 추출","사용자 메모"];
inputs.getRange("A4:O4").values = [sourceHeaders]; header(inputs.getRange("A4:O4"));
inputs.getRange(`A5:O${4+data.metrics.length}`).values = data.metrics.map(m => [m.year,m.revenue,m.operating_profit,m.net_income,m.cfo,m.ar,m.inventory,m.ap,m.cash,m.cogs,m.debt,m.lease,"전자공시 재무제표 조회","예",""]);
inputs.getRange(`B5:L${4+data.metrics.length}`).format = { numberFormat: amountFmt, font: { color: "#008000" } };
inputs.getRange(`O5:O${4+data.metrics.length}`).format = { fill: amber, font: { color: "#0000FF" } };
inputs.freezePanes.freezeRows(4); widths(inputs,{A:10,B:24,C:24,D:24,E:24,F:24,G:24,H:24,I:24,J:24,K:24,L:24,M:26,N:12,O:28});

const lastCol = excelCol(1 + data.years.length);
const summaryEndCol = excelCol(Math.max(1 + data.years.length, 8));
title(summary, "QoE 요약 | 보고이익·정상화 손익·현금전환·순차입금", summaryEndCol);
summary.getRange("A4").values = [["지표"]];
summary.getRange("A5:A17").values = [["매출액"],["매출 성장률"],["보고 영업이익"],["보고 영업이익률"],["정상화 영업이익(사용자 조정)"],["정상화 영업이익률(사용자 조정)"],["영업현금흐름"],["영업이익 대비 영업현금흐름"],["순이익"],["순이익–영업현금흐름 괴리"],["순차입금"],["검토 후보 수"],["정상화 조정 반영 건수"]];
summary.getRange(`B4:${lastCol}4`).values = [data.years];
summary.getRange(`B4:${lastCol}4`).format = { fill: pale, font: { bold: true }, horizontalAlignment: "right" };
data.years.forEach((y,i) => {
  const c = excelCol(2+i), src = 5+i;
  summary.getRange(`${c}5:${c}17`).formulas = [[`='원천 자료'!B${src}`],[i?`=IFERROR(${c}5/${excelCol(1+i)}5-1,"")`:""],[`='원천 자료'!C${src}`],[`=IFERROR(${c}7/${c}5,"")`],[`='정상화 손익'!${c}9`],[`='정상화 손익'!${c}11`],[`='원천 자료'!E${src}`],[`=IFERROR(${c}11/${c}7,"")`],[`='원천 자료'!D${src}`],[`=${c}13-${c}11`],[`='원천 자료'!K${src}+IF('표지'!$B$10="순차입금에 포함",'원천 자료'!L${src},0)-'원천 자료'!I${src}`],[`=COUNTIF('검토 후보'!$A$5:$A$204,${c}$4)`],[`=COUNTIFS('검토 후보'!$A$5:$A$204,${c}$4,'검토 후보'!$K$5:$K$204,"조정")`]];
  summary.getRange(`${c}5:${c}17`).format.font = { color: "#000000" };
});
summary.getRange(`B5:${lastCol}5`).format.numberFormat = amountFmt; summary.getRange(`B6:${lastCol}6`).format.numberFormat = pctFmt;
summary.getRange(`B7:${lastCol}7`).format.numberFormat = amountFmt; summary.getRange(`B8:${lastCol}8`).format.numberFormat = pctFmt;
summary.getRange(`B9:${lastCol}9`).format.numberFormat = amountFmt; summary.getRange(`B10:${lastCol}10`).format.numberFormat = pctFmt;
summary.getRange(`B11:${lastCol}11`).format.numberFormat = amountFmt; summary.getRange(`B12:${lastCol}12`).format.numberFormat = pctFmt;
summary.getRange(`B13:${lastCol}15`).format.numberFormat = amountFmt;
summary.getRange("A7:F7").format.borders = { top: { style:"thin", color:navy } };
summary.getRange(`A18:${summaryEndCol}18`).merge(); summary.getRange("A18").values = [["정상화 영업이익은 검토 후보 시트에서 사용자가 '조정' 여부, 가산·차감 방향과 적용 금액을 입력한 항목만 반영합니다. 미결정 항목은 자동으로 제외하지 않습니다."]]; summary.getRange(`A18:${summaryEndCol}18`).format = { fill: amber, wrapText:true, rowHeight:32 };
// Keep the chart source and chart below the horizontal summary so longer
// analysis periods can add year columns without overlapping either area.
const chartSourceHeaderRow = 23;
const chartSourceFirstRow = chartSourceHeaderRow + 1;
const chartSourceLastRow = chartSourceHeaderRow + data.years.length;
summary.getRange(`A${chartSourceHeaderRow}:D${chartSourceHeaderRow}`).values = [["연도","보고 영업이익률","정상화 영업이익률","영업현금흐름/영업이익"]];
data.years.forEach((y,i)=>{ const row=chartSourceFirstRow+i, src=excelCol(2+i); summary.getRange(`A${row}:D${row}`).formulas=[[`=${src}$4`,`=${src}$8`,`=${src}$10`,`=${src}$12`]]; });
const chart = summary.charts.add("line", summary.getRange(`A${chartSourceHeaderRow}:D${chartSourceLastRow}`));
chart.title="보고·정상화 이익률과 현금전환 추이"; chart.hasLegend=true; chart.yAxis={numberFormatCode:"0%"}; chart.setPosition(`F${chartSourceHeaderRow}`,`M${chartSourceHeaderRow+13}`);
summary.freezePanes.freezeRows(4); widths(summary,{A:36,B:16,C:16,D:16,E:16,F:16,G:3,H:16,I:3,J:16,K:16,L:16,M:16,N:16,O:16,P:16,Q:16});
data.years.forEach((_,i)=>{ const c=excelCol(2+i); summary.getRange(`${c}:${c}`).format.columnWidth=22; });

const wcEndCol = excelCol(Math.max(1 + data.years.length, 10));
title(wc, "운전자본 | 회전일수와 순운전자본", wcEndCol);
wc.getRange("A4").values = [["지표"]];
wc.getRange("A5:A12").values = [["매출액"],["매출채권회전일수"],["재고자산회전일수"],["매입채무회전일수"],["현금전환주기(참고)"],["순운전자본"],["매출 대비 순운전자본"],["매출채권·재고 증가율과 매출 증가율 차이"]];
wc.getRange(`B4:${lastCol}4`).values=[data.years]; wc.getRange(`B4:${lastCol}4`).format={fill:pale,font:{bold:true},horizontalAlignment:"right"};
data.years.forEach((_,i)=>{ const c=excelCol(2+i), src=5+i, prev=excelCol(1+i);
  wc.getRange(`${c}5:${c}12`).formulas=[[`='원천 자료'!B${src}`],[`=IFERROR(AVERAGE('원천 자료'!F${Math.max(5,src-1)}:'원천 자료'!F${src})/'원천 자료'!B${src}*365,"")`],[`=IFERROR(AVERAGE('원천 자료'!G${Math.max(5,src-1)}:'원천 자료'!G${src})/'원천 자료'!J${src}*365,"")`],[`=IFERROR(AVERAGE('원천 자료'!H${Math.max(5,src-1)}:'원천 자료'!H${src})/'원천 자료'!J${src}*365,"")`],[`=${c}6+${c}7-${c}8`],[`='원천 자료'!F${src}+'원천 자료'!G${src}-'원천 자료'!H${src}`],[`=IFERROR(${c}10/${c}5,"")`],[i?`=IFERROR((('원천 자료'!F${src}+'원천 자료'!G${src})/('원천 자료'!F${src-1}+'원천 자료'!G${src-1})-1)-(${c}5/${prev}5-1),"")`:""]];
});
wc.getRange(`B5:${lastCol}5`).format.numberFormat=amountFmt; wc.getRange(`B6:${lastCol}9`).format.numberFormat=daysFmt; wc.getRange(`B10:${lastCol}10`).format.numberFormat=amountFmt; wc.getRange(`B11:${lastCol}12`).format.numberFormat=pctFmt;
wc.getRange(`A14:${wcEndCol}14`).merge(); wc.getRange("A14").values=[["운전자본 검토 포인트: 매출채권·재고 증가율이 매출 증가율을 크게 초과하거나 현금전환주기가 연속 상승하면 계약조건, 재고 진부화, 매출 인식 시점을 공시 원문과 함께 확인하세요."]]; wc.getRange(`A14:${wcEndCol}14`).format={fill:amber,wrapText:true,rowHeight:32,verticalAlignment:"center"};
wc.freezePanes.freezeRows(4); widths(wc,{A:36,B:18,C:18,D:18,E:18,F:18,G:16,H:16,I:16,J:16});
data.years.forEach((_,i)=>{ const c=excelCol(2+i); wc.getRange(`${c}:${c}`).format.columnWidth=22; });

const netDebtEndCol = excelCol(Math.max(1 + data.years.length, 8));
title(netDebt, "순차입금 | 현금·차입금·리스부채 구성", netDebtEndCol);
netDebt.getRange("A4").values = [["지표"]];
netDebt.getRange("A5:A12").values = [["현금및현금성자산"],["차입금·사채"],["리스부채"],["순차입금 산정 대상 부채"],["순차입금"],["순현금"],["전년 대비 순차입금 증감"],["산정 대상 부채/현금"]];
netDebt.getRange(`B4:${lastCol}4`).values=[data.years];
netDebt.getRange(`B4:${lastCol}4`).format={fill:pale,font:{bold:true},horizontalAlignment:"right"};
data.years.forEach((_,i)=>{ const c=excelCol(2+i), src=5+i, prev=excelCol(1+i);
  netDebt.getRange(`${c}5:${c}12`).formulas=[
    [`='원천 자료'!I${src}`],
    [`='원천 자료'!K${src}`],
    [`='원천 자료'!L${src}`],
    [`=${c}6+IF('표지'!$B$10="순차입금에 포함",${c}7,0)`],
    [`=${c}8-${c}5`],
    [`=${c}5-${c}8`],
    [i?`=${c}9-${prev}9`:""],
    [`=IFERROR(${c}8/${c}5,"")`],
  ];
  netDebt.getRange(`${c}:${c}`).format.columnWidth=22;
});
netDebt.getRange(`B5:${lastCol}11`).format.numberFormat=amountFmt;
netDebt.getRange(`B12:${lastCol}12`).format.numberFormat=multipleFmt;
netDebt.getRange(`A8:${lastCol}8`).format.borders={top:{style:"thin",color:navy}};
netDebt.getRange(`A9:${lastCol}10`).format={fill:pale,font:{bold:true,color:"#000000"}};
netDebt.getRange(`A14:${netDebtEndCol}14`).merge();
netDebt.getRange("A14").values=[["순차입금 = 차입금·사채 + 선택 시 리스부채 - 현금및현금성자산입니다. 순현금은 부호를 반대로 표시하며, 리스부채 포함 여부는 표지의 선택값과 연결됩니다."]];
netDebt.getRange(`A14:${netDebtEndCol}14`).format={fill:amber,wrapText:true,rowHeight:32,verticalAlignment:"center"};
netDebt.freezePanes.freezeRows(4);
widths(netDebt,{A:34});

title(candidates, "검토 후보 | 일회성 손익·정상화 조정 검토 (결론 아님)", "N");
const candHeaders=["연도","유형","계정/키워드","자동 추출 금액","원문 발췌","접수번호","DART 원문","추출 방식","자동 추출","검토 상태","사용자 조정 여부","조정 방향","적용 금액","조정 사유"];
candidates.getRange("A4:N4").values=[candHeaders]; header(candidates.getRange("A4:N4"));
const candEnd=4+Math.max((data.candidates||[]).slice(0,200).length,1);
candidates.getRange(`F5:F${candEnd}`).format.numberFormat="0";
const candRows=(data.candidates||[]).slice(0,200).map(x=>[x.year,x.category,x.account,x.amount,x.excerpt,x.rcept_no||"",x.rcept_no?`https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${x.rcept_no}`:"",x.source,"예",x.status,x.user_adjustment||"미결정",x.adjustment_direction||"미결정",x.applied_amount??x.amount,x.adjustment_reason||""]);
if(candRows.length) candidates.getRange(`A5:N${4+candRows.length}`).values=candRows;
candidates.getRange(`D5:D${candEnd}`).format.numberFormat=amountFmt; candidates.getRange(`E5:E${candEnd}`).format={wrapText:true,rowHeight:34};
candidates.getRange(`M5:M${candEnd}`).format.numberFormat=amountFmt;
candidates.getRange(`K5:N${candEnd}`).format={fill:amber,font:{color:"#0000FF"},wrapText:true};
candidates.getRange(`J5:J${candEnd}`).dataValidation={rule:{type:"list",values:["확인 필요","검토 완료","제외"]}};
candidates.getRange(`K5:K${candEnd}`).dataValidation={rule:{type:"list",values:["미결정","조정","조정 안 함"]}};
candidates.getRange(`L5:L${candEnd}`).dataValidation={rule:{type:"list",values:["미결정","가산","차감"]}};
candidates.freezePanes.freezeRows(4); widths(candidates,{A:9,B:26,C:24,D:22,E:62,F:18,G:42,H:17,I:12,J:14,K:18,L:15,M:22,N:42});

const normalizedEndCol = excelCol(Math.max(1 + data.years.length, 9));
title(normalized, "정상화 손익 | 사용자 검토를 반영한 영업이익", normalizedEndCol);
normalized.getRange("A4").values=[["지표"]];
normalized.getRange("A5:A12").values=[["보고 영업이익"],["일회성 손실 가산"],["일회성 이익 차감"],["정상화 순조정액"],["정상화 영업이익"],["보고 영업이익률"],["정상화 영업이익률"],["조정 반영 건수"]];
normalized.getRange(`B4:${lastCol}4`).values=[data.years];
normalized.getRange(`B4:${lastCol}4`).format={fill:pale,font:{bold:true},horizontalAlignment:"right"};
data.years.forEach((_,i)=>{const c=excelCol(2+i),src=5+i;
  normalized.getRange(`${c}5:${c}12`).formulas=[
    [`='원천 자료'!C${src}`],
    [`=SUMIFS('검토 후보'!$M$5:$M$204,'검토 후보'!$A$5:$A$204,${c}$4,'검토 후보'!$K$5:$K$204,"조정",'검토 후보'!$L$5:$L$204,"가산")`],
    [`=SUMIFS('검토 후보'!$M$5:$M$204,'검토 후보'!$A$5:$A$204,${c}$4,'검토 후보'!$K$5:$K$204,"조정",'검토 후보'!$L$5:$L$204,"차감")`],
    [`=${c}6-${c}7`],
    [`=${c}5+${c}8`],
    [`=IFERROR(${c}5/'원천 자료'!B${src},"")`],
    [`=IFERROR(${c}9/'원천 자료'!B${src},"")`],
    [`=COUNTIFS('검토 후보'!$A$5:$A$204,${c}$4,'검토 후보'!$K$5:$K$204,"조정")`],
  ];
  normalized.getRange(`${c}:${c}`).format.columnWidth=22;
});
normalized.getRange(`B5:${lastCol}9`).format.numberFormat=amountFmt;
normalized.getRange(`B10:${lastCol}11`).format.numberFormat=pctFmt;
normalized.getRange(`A8:${lastCol}8`).format.borders={top:{style:"thin",color:navy}};
normalized.getRange(`A9:${lastCol}11`).format={fill:pale,font:{bold:true,color:"#000000"}};
normalized.getRange(`A14:${normalizedEndCol}14`).merge();
normalized.getRange("A14").values=[["사용 방법: 검토 후보 시트에서 사용자 조정 여부를 '조정'으로 선택하고, 조정 방향(손실 제거=가산, 이익 제거=차감)과 적용 금액을 확인·수정합니다. 자동 추출 금액은 참고값이며 미결정 항목은 정상화 손익에 반영되지 않습니다."]];
normalized.getRange(`A14:${normalizedEndCol}14`).format={fill:amber,wrapText:true,rowHeight:38,verticalAlignment:"center"};
normalized.getRange("A17:I17").values=[["연도","유형","계정/키워드","자동 추출 금액","사용자 조정 여부","조정 방향","적용 금액","조정 사유","DART 원문"]];
header(normalized.getRange("A17:I17"));
const normalizedCandidateCount=(data.candidates||[]).slice(0,200).length;
if(normalizedCandidateCount){
  for(let i=0;i<normalizedCandidateCount;i++){const row=18+i,source=5+i;
    normalized.getRange(`A${row}:I${row}`).formulas=[[`='검토 후보'!A${source}`,`='검토 후보'!B${source}`,`='검토 후보'!C${source}`,`='검토 후보'!D${source}`,`='검토 후보'!K${source}`,`='검토 후보'!L${source}`,`='검토 후보'!M${source}`,`='검토 후보'!N${source}`,`='검토 후보'!G${source}`]];
  }
  normalized.getRange(`D18:D${17+normalizedCandidateCount}`).format.numberFormat=amountFmt;
  normalized.getRange(`G18:G${17+normalizedCandidateCount}`).format.numberFormat=amountFmt;
  normalized.getRange(`H18:I${17+normalizedCandidateCount}`).format.wrapText=true;
}else{
  normalized.getRange("A18:I18").merge();
  normalized.getRange("A18").values=[["탐지된 검토 후보가 없습니다. 원문과 계정 매핑의 완전성을 별도로 확인하세요."]];
}
normalized.freezePanes.freezeRows(4);
widths(normalized,{A:34,B:24,C:24,D:22,E:18,F:15,G:22,H:38,I:42});

title(audit,"검토 흔적 | 사용 공시·계정·산식", "J");
audit.getRange("A4:J4").values=[["연도","사용 공시","접수일","접수번호","원문 주소","재무제표 기준","사용 계정","계산식","자동 추출","비고"]]; header(audit.getRange("A4:J4"));
const filingMap=Object.fromEntries((data.filings||[]).map(x=>[x.year,x]));
audit.getRange(`C5:D${4+data.years.length}`).format.numberFormat="0";
audit.getRange(`A5:J${4+data.years.length}`).values=data.years.map(y=>{const f=filingMap[y]||{};return[y,f.report_nm||"사업보고서",f.rcept_dt||"",f.rcept_no||"",f.url||"",data.metadata.basis,"원천 자료 시트 참조","각 분석 시트의 셀 수식 참조","예",""];});
const formulaHeaderRow = 6 + data.years.length;
audit.getRange(`A${formulaHeaderRow}:J${formulaHeaderRow}`).merge(); audit.getRange(`A${formulaHeaderRow}`).values=[["산식 정의"]]; header(audit.getRange(`A${formulaHeaderRow}:J${formulaHeaderRow}`));
audit.getRange(`A${formulaHeaderRow+1}:B${formulaHeaderRow+12}`).values=[["영업이익률","영업이익 / 매출액"],["영업이익 대비 영업현금흐름","영업활동현금흐름 / 영업이익"],["매출채권회전일수","평균 매출채권 / 매출액 × 365"],["재고자산회전일수","평균 재고자산 / 매출원가 × 365"],["매입채무회전일수","평균 매입채무 / 매출원가 × 365"],["순운전자본","매출채권 + 재고자산 - 매입채무"],["순차입금","차입금·사채 + 선택 시 리스부채 - 현금"],["순현금","현금 - 차입금·사채 - 선택 시 리스부채"],["정상화 후보","계정명·원문 키워드 자동 탐지 후 사용자 판단"],["정상화 순조정액","사용자 선택 가산 조정액 - 차감 조정액"],["정상화 영업이익","보고 영업이익 + 정상화 순조정액"],["정상화 영업이익률","정상화 영업이익 / 매출액"]];
audit.freezePanes.freezeRows(4); widths(audit,{A:9,B:27,C:13,D:18,E:45,F:18,G:26,H:42,I:12,J:35});

title(checks,"검증 | 완전성·산식·한계", "G");
checks.getRange("A4:G4").values=[["검사항목","실제값","기대값","차이","허용범위","상태","비고"]]; header(checks.getRange("A4:G4"));
checks.getRange("A5:G9").values=[
  ["분석기간 수",data.years.length,"사용자 설정",0,0,data.years.length>=1?"정상":"검토 필요","선택한 전체 사업연도를 분석"],
  ["연결재무제표",data.metadata.basis,"연결재무제표",0,0,data.metadata.basis.includes("연결")?"정상":"검토 필요","연결 우선 원칙"],
  ["매출 누락",data.metrics.filter(x=>x.revenue==null).length,0,data.metrics.filter(x=>x.revenue==null).length,0,data.metrics.every(x=>x.revenue!=null)?"정상":"검토 필요","원천 자료 확인"],
  ["영업이익 누락",data.metrics.filter(x=>x.operating_profit==null).length,0,data.metrics.filter(x=>x.operating_profit==null).length,0,data.metrics.every(x=>x.operating_profit!=null)?"정상":"검토 필요","원천 자료 확인"],
  ["원문 수집 오류",(data.errors||[]).length,0,(data.errors||[]).length,0,(data.errors||[]).length===0?"정상":"검토 필요","오류는 제외하지 않고 표시"],
];
checks.getRange("A10:G10").values=[["순차입금 연계",null,null,null,null,null,"순차입금 시트와 QoE 요약 일치 여부"]];
checks.getRange("B10:F10").formulas=[[`='순차입금'!${lastCol}9`,`='QoE 요약'!${lastCol}15`,`=B10-C10`,"=0",'=IF(ABS(D10)<=E10,"정상","검토 필요")']];
checks.getRange("A11:G11").values=[["정상화 손익 연계",null,null,null,null,null,"정상화 손익 시트와 QoE 요약 일치 여부"]];
checks.getRange("B11:F11").formulas=[[`='정상화 손익'!${lastCol}9`,`='QoE 요약'!${lastCol}9`,`=B11-C11`,"=0",'=IF(ABS(D11)<=E11,"정상","검토 필요")']];
checks.getRange("B10:E11").format.numberFormat=amountFmt;
checks.getRange("F5:F11").conditionalFormats.add("containsText",{text:"정상",format:{fill:green,font:{bold:true,color:"#006100"}}});
checks.getRange("F5:F11").conditionalFormats.add("containsText",{text:"검토 필요",format:{fill:red,font:{bold:true,color:"#9C0006"}}});
checks.getRange("A13:G13").merge(); checks.getRange("A13").values=[["제한사항"]]; header(checks.getRange("A13:G13"));
data.limitations.forEach((x,i)=>{const row=14+i; checks.getRange(`A${row}:G${row}`).merge(); checks.getRange(`A${row}`).values=[[x]]; checks.getRange(`A${row}:G${row}`).format={wrapText:true,rowHeight:26};});
widths(checks,{A:28,B:18,C:18,D:15,E:15,F:15,G:44});

for (const s of [cover,inputs,summary,normalized,wc,netDebt,candidates,audit,checks]) { s.getUsedRange().format.font.name="Aptos"; }
await fs.mkdir(new URL(".",`file:///${outputPath.replaceAll("\\","/")}`).pathname,{recursive:true}).catch(()=>{});
const xlsx=await SpreadsheetFile.exportXlsx(wb); await xlsx.save(outputPath);
const inspection=await wb.inspect({kind:"table",range:"'QoE 요약'!A1:F18",include:"values,formulas",tableMaxRows:22,tableMaxCols:8});
const normalizedInspection=await wb.inspect({kind:"table",range:`'정상화 손익'!A1:${lastCol}14`,include:"values,formulas",tableMaxRows:20,tableMaxCols:12});
const netDebtInspection=await wb.inspect({kind:"table",range:`'순차입금'!A1:${lastCol}14`,include:"values,formulas",tableMaxRows:20,tableMaxCols:12});
const errors=await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:200},summary:"수식 오류 검사"});
console.log(inspection.ndjson); console.log(normalizedInspection.ndjson); console.log(netDebtInspection.ndjson); console.log(errors.ndjson);
if(previewDir){await fs.mkdir(previewDir,{recursive:true}); for(const s of ["표지","원천 자료","QoE 요약","정상화 손익","운전자본","순차입금","검토 후보","검토 흔적","검증"]){const blob=await wb.render({sheetName:s,autoCrop:"all",scale:1,format:"png"}); await fs.writeFile(`${previewDir}/${s.replaceAll(" ","_")}.png`,new Uint8Array(await blob.arrayBuffer()));}}
