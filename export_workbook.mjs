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
const wc = wb.worksheets.add("운전자본");
const candidates = wb.worksheets.add("검토 후보");
const audit = wb.worksheets.add("검토 흔적");
const checks = wb.worksheets.add("검증");

const navy = "#112A46", blue = "#2F5597", pale = "#DDEBF7", green = "#E2F0D9", amber = "#FFF2CC", red = "#FCE4D6", grey = "#F2F2F2";
const amountFmt = '#,##0;[Red](#,##0);-';
const pctFmt = '0.0%;[Red](0.0%);-';
const daysFmt = '0.0"일";[Red](0.0"일");-';
function title(sheet, text, endCol="H") {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${endCol}2`).merge();
  sheet.getRange("A1").values = [[text]];
  sheet.getRange(`A1:${endCol}2`).format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 18 }, verticalAlignment: "center" };
}
function header(range) { range.format = { fill: blue, font: { bold: true, color: "#FFFFFF" }, verticalAlignment: "center", wrapText: true, borders: { preset: "inside", style: "thin", color: "#D9E2F3" } }; }
function widths(sheet, specs) { for (const [col, width] of Object.entries(specs)) sheet.getRange(`${col}:${col}`).format.columnWidth = width; }

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
cover.getRange("A15:J18").merge(); cover.getRange("A15").values = [["1) QoE 요약에서 이익·현금흐름 추이를 확인합니다.  2) 운전자본에서 회전일수와 매출 대비 운전자본 변화를 봅니다.  3) 검토 후보에서 자동 탐지 항목의 원문을 확인하고 사용자 조정 여부·사유를 기록합니다.  4) 검토 흔적과 검증에서 출처·산식·오류를 확인합니다."]];
cover.getRange("A15:J18").format = { wrapText: true, verticalAlignment: "top" };
widths(cover, {A:20,B:22,C:3,D:17,E:17,F:17,G:17,H:17,I:17,J:17});

title(inputs, "원천 자료 | 공시 추출값", "O");
const sourceHeaders = ["연도","매출액","영업이익","순이익","영업현금흐름","매출채권","재고자산","매입채무","현금및현금성자산","매출원가","차입금·사채","리스부채","추출 기준","자동 추출","사용자 메모"];
inputs.getRange("A4:O4").values = [sourceHeaders]; header(inputs.getRange("A4:O4"));
inputs.getRange(`A5:O${4+data.metrics.length}`).values = data.metrics.map(m => [m.year,m.revenue,m.operating_profit,m.net_income,m.cfo,m.ar,m.inventory,m.ap,m.cash,m.cogs,m.debt,m.lease,"전자공시 재무제표 조회","예",""]);
inputs.getRange(`B5:L${4+data.metrics.length}`).format = { numberFormat: amountFmt, font: { color: "#008000" } };
inputs.getRange(`O5:O${4+data.metrics.length}`).format = { fill: amber, font: { color: "#0000FF" } };
inputs.freezePanes.freezeRows(4); widths(inputs,{A:10,B:17,C:17,D:17,E:19,F:17,G:17,H:17,I:20,J:17,K:18,L:16,M:26,N:12,O:28});

title(summary, "QoE 요약 | 보고이익·현금전환·순차입금", "F");
summary.getRange("A4").values = [["지표"]];
summary.getRange("A5:A14").values = [["매출액"],["매출 성장률"],["영업이익"],["영업이익률"],["영업현금흐름"],["영업이익 대비 영업현금흐름"],["순이익"],["순이익–영업현금흐름 괴리"],["순차입금"],["검토 후보 수"]];
summary.getRange(`B4:${String.fromCharCode(65+data.years.length)}4`).values = [data.years];
const lastCol = String.fromCharCode(65+data.years.length);
summary.getRange(`B4:${lastCol}4`).format = { fill: pale, font: { bold: true }, horizontalAlignment: "right" };
data.years.forEach((y,i) => {
  const c = String.fromCharCode(66+i), src = 5+i;
  summary.getRange(`${c}5:${c}14`).formulas = [[`='원천 자료'!B${src}`],[i?`=IFERROR(${c}5/${String.fromCharCode(65+i)}5-1,"")`:""],[`='원천 자료'!C${src}`],[`=IFERROR(${c}7/${c}5,"")`],[`='원천 자료'!E${src}`],[`=IFERROR(${c}9/${c}7,"")`],[`='원천 자료'!D${src}`],[`=${c}11-${c}9`],[`='원천 자료'!K${src}+IF('표지'!$B$10="순차입금에 포함",'원천 자료'!L${src},0)-'원천 자료'!I${src}`],[`=COUNTIF('검토 후보'!$A$5:$A$204,${c}$4)`]];
  summary.getRange(`${c}5:${c}14`).format.font = { color: "#000000" };
});
summary.getRange(`B5:${lastCol}5`).format.numberFormat = amountFmt; summary.getRange(`B6:${lastCol}6`).format.numberFormat = pctFmt;
summary.getRange(`B7:${lastCol}7`).format.numberFormat = amountFmt; summary.getRange(`B8:${lastCol}8`).format.numberFormat = pctFmt;
summary.getRange(`B9:${lastCol}9`).format.numberFormat = amountFmt; summary.getRange(`B10:${lastCol}10`).format.numberFormat = pctFmt;
summary.getRange(`B11:${lastCol}13`).format.numberFormat = amountFmt;
summary.getRange("A7:F7").format.borders = { top: { style:"thin", color:navy } };
summary.getRange("A15:H15").merge(); summary.getRange("A15").values = [["해석 주의: 영업이익 대비 영업현금흐름 저하, 순이익–영업현금흐름 괴리 확대, 순차입금 증가는 결론이 아니라 추가 질문의 출발점입니다."]]; summary.getRange("A15:H15").format = { fill: amber, wrapText:true, rowHeight:30 };
// Keep the formula-backed chart source below the chart so longer analysis periods
// can extend downward without colliding with the summary or chart area.
const chartSourceHeaderRow = 20;
const chartSourceFirstRow = chartSourceHeaderRow + 1;
const chartSourceLastRow = chartSourceHeaderRow + data.years.length;
summary.getRange(`J${chartSourceHeaderRow}:L${chartSourceHeaderRow}`).values = [["연도","영업이익률","영업현금흐름/영업이익"]];
data.years.forEach((y,i)=>{ const row=chartSourceFirstRow+i, src=String.fromCharCode(66+i); summary.getRange(`J${row}:L${row}`).formulas=[[`=${src}$4`,`=${src}$8`,`=${src}$10`]]; });
const chart = summary.charts.add("line", summary.getRange(`J${chartSourceHeaderRow}:L${chartSourceLastRow}`));
chart.title="이익률과 현금전환 추이"; chart.hasLegend=true; chart.yAxis={numberFormatCode:"0%"}; chart.setPosition("J4","Q17");
summary.freezePanes.freezeRows(4); widths(summary,{A:28,B:16,C:16,D:16,E:16,F:16,G:3,H:16,I:3,J:16,K:16,L:16,M:16,N:16,O:16,P:16,Q:16});

title(wc, "운전자본 | 회전일수와 순운전자본", "F");
wc.getRange("A4").values = [["지표"]];
wc.getRange("A5:A12").values = [["매출액"],["매출채권회전일수"],["재고자산회전일수"],["매입채무회전일수"],["현금전환주기(참고)"],["순운전자본"],["매출 대비 순운전자본"],["매출채권·재고 증가율과 매출 증가율 차이"]];
wc.getRange(`B4:${lastCol}4`).values=[data.years]; wc.getRange(`B4:${lastCol}4`).format={fill:pale,font:{bold:true},horizontalAlignment:"right"};
data.years.forEach((_,i)=>{ const c=String.fromCharCode(66+i), src=5+i, prev=String.fromCharCode(65+i);
  wc.getRange(`${c}5:${c}12`).formulas=[[`='원천 자료'!B${src}`],[`=IFERROR(AVERAGE('원천 자료'!F${Math.max(5,src-1)}:'원천 자료'!F${src})/'원천 자료'!B${src}*365,"")`],[`=IFERROR(AVERAGE('원천 자료'!G${Math.max(5,src-1)}:'원천 자료'!G${src})/'원천 자료'!J${src}*365,"")`],[`=IFERROR(AVERAGE('원천 자료'!H${Math.max(5,src-1)}:'원천 자료'!H${src})/'원천 자료'!J${src}*365,"")`],[`=${c}6+${c}7-${c}8`],[`='원천 자료'!F${src}+'원천 자료'!G${src}-'원천 자료'!H${src}`],[`=IFERROR(${c}10/${c}5,"")`],[i?`=IFERROR((('원천 자료'!F${src}+'원천 자료'!G${src})/('원천 자료'!F${src-1}+'원천 자료'!G${src-1})-1)-(${c}5/${prev}5-1),"")`:""]];
});
wc.getRange(`B5:${lastCol}5`).format.numberFormat=amountFmt; wc.getRange(`B6:${lastCol}9`).format.numberFormat=daysFmt; wc.getRange(`B10:${lastCol}10`).format.numberFormat=amountFmt; wc.getRange(`B11:${lastCol}12`).format.numberFormat=pctFmt;
wc.getRange("A14:F14").merge(); wc.getRange("A14").values=[["주의 신호 예시: 매출채권·재고 증가율이 매출 증가율을 크게 초과하거나, 현금전환주기가 연속 상승하는 경우 계약조건·재고 진부화·매출 인식 시점을 원문과 함께 확인합니다."]]; wc.getRange("A14:F14").format={fill:amber,wrapText:true};
wc.freezePanes.freezeRows(4); widths(wc,{A:36,B:18,C:18,D:18,E:18,F:18});

title(candidates, "검토 후보 | 정상화 조정 후보 (결론 아님)", "L");
const candHeaders=["연도","유형","계정/키워드","금액","원문 발췌","접수번호","DART 원문","추출 방식","자동 추출","검토 상태","사용자 조정 여부","조정 사유"];
candidates.getRange("A4:L4").values=[candHeaders]; header(candidates.getRange("A4:L4"));
const candEnd=4+Math.max((data.candidates||[]).slice(0,200).length,1);
candidates.getRange(`F5:F${candEnd}`).format.numberFormat="0";
const candRows=(data.candidates||[]).slice(0,200).map(x=>[x.year,x.category,x.account,x.amount,x.excerpt,x.rcept_no||"",x.rcept_no?`https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${x.rcept_no}`:"",x.source,"예",x.status,"미결정",""]);
if(candRows.length) candidates.getRange(`A5:L${4+candRows.length}`).values=candRows;
candidates.getRange(`D5:D${candEnd}`).format.numberFormat=amountFmt; candidates.getRange(`E5:E${candEnd}`).format={wrapText:true,rowHeight:34};
candidates.getRange(`K5:L${candEnd}`).format={fill:amber,font:{color:"#0000FF"},wrapText:true};
candidates.getRange(`J5:J${candEnd}`).dataValidation={rule:{type:"list",values:["확인 필요","검토 완료","제외"]}};
candidates.getRange(`K5:K${candEnd}`).dataValidation={rule:{type:"list",values:["미결정","조정","조정 안 함"]}};
candidates.freezePanes.freezeRows(4); widths(candidates,{A:9,B:26,C:24,D:17,E:62,F:18,G:42,H:17,I:12,J:14,K:18,L:42});

title(audit,"검토 흔적 | 사용 공시·계정·산식", "J");
audit.getRange("A4:J4").values=[["연도","사용 공시","접수일","접수번호","원문 주소","재무제표 기준","사용 계정","계산식","자동 추출","비고"]]; header(audit.getRange("A4:J4"));
const filingMap=Object.fromEntries((data.filings||[]).map(x=>[x.year,x]));
audit.getRange(`C5:D${4+data.years.length}`).format.numberFormat="0";
audit.getRange(`A5:J${4+data.years.length}`).values=data.years.map(y=>{const f=filingMap[y]||{};return[y,f.report_nm||"사업보고서",f.rcept_dt||"",f.rcept_no||"",f.url||"",data.metadata.basis,"원천 자료 시트 참조","각 분석 시트의 셀 수식 참조","예",""];});
audit.getRange("A11:J11").merge(); audit.getRange("A11").values=[["산식 정의"]]; header(audit.getRange("A11:J11"));
audit.getRange("A12:B19").values=[["영업이익률","영업이익 / 매출액"],["영업이익 대비 영업현금흐름","영업활동현금흐름 / 영업이익"],["매출채권회전일수","평균 매출채권 / 매출액 × 365"],["재고자산회전일수","평균 재고자산 / 매출원가 × 365"],["매입채무회전일수","평균 매입채무 / 매출원가 × 365"],["순운전자본","매출채권 + 재고자산 - 매입채무"],["순차입금","차입금·사채 + 선택 시 리스부채 - 현금"],["정상화 후보","계정명·원문 키워드 자동 탐지 후 사용자 판단"]];
audit.freezePanes.freezeRows(4); widths(audit,{A:9,B:27,C:13,D:18,E:45,F:18,G:26,H:42,I:12,J:35});

title(checks,"검증 | 완전성·산식·한계", "G");
checks.getRange("A4:G4").values=[["검사항목","실제값","기대값","차이","허용범위","상태","비고"]]; header(checks.getRange("A4:G4"));
checks.getRange("A5:G9").values=[
  ["분석기간 수",data.years.length,"3개년 권장",0,0,data.years.length>=2?"정상":"검토 필요","개념검증은 3개 사업연도 권장"],
  ["연결재무제표",data.metadata.basis,"연결재무제표",0,0,data.metadata.basis.includes("연결")?"정상":"검토 필요","연결 우선 원칙"],
  ["매출 누락",data.metrics.filter(x=>x.revenue==null).length,0,data.metrics.filter(x=>x.revenue==null).length,0,data.metrics.every(x=>x.revenue!=null)?"정상":"검토 필요","원천 자료 확인"],
  ["영업이익 누락",data.metrics.filter(x=>x.operating_profit==null).length,0,data.metrics.filter(x=>x.operating_profit==null).length,0,data.metrics.every(x=>x.operating_profit!=null)?"정상":"검토 필요","원천 자료 확인"],
  ["원문 수집 오류",(data.errors||[]).length,0,(data.errors||[]).length,0,(data.errors||[]).length===0?"정상":"검토 필요","오류는 제외하지 않고 표시"],
];
checks.getRange("F5:F9").conditionalFormats.add("containsText",{text:"정상",format:{fill:green,font:{bold:true,color:"#006100"}}});
checks.getRange("F5:F9").conditionalFormats.add("containsText",{text:"검토 필요",format:{fill:red,font:{bold:true,color:"#9C0006"}}});
checks.getRange("A12:G12").merge(); checks.getRange("A12").values=[["제한사항"]]; header(checks.getRange("A12:G12"));
data.limitations.forEach((x,i)=>{const row=13+i; checks.getRange(`A${row}:G${row}`).merge(); checks.getRange(`A${row}`).values=[[x]]; checks.getRange(`A${row}:G${row}`).format={wrapText:true,rowHeight:26};});
widths(checks,{A:28,B:18,C:18,D:15,E:15,F:15,G:44});

for (const s of [cover,inputs,summary,wc,candidates,audit,checks]) { s.getUsedRange().format.font.name="Aptos"; }
await fs.mkdir(new URL(".",`file:///${outputPath.replaceAll("\\","/")}`).pathname,{recursive:true}).catch(()=>{});
const xlsx=await SpreadsheetFile.exportXlsx(wb); await xlsx.save(outputPath);
const inspection=await wb.inspect({kind:"table",range:"'QoE 요약'!A1:F15",include:"values,formulas",tableMaxRows:20,tableMaxCols:8});
const errors=await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:200},summary:"수식 오류 검사"});
console.log(inspection.ndjson); console.log(errors.ndjson);
if(previewDir){await fs.mkdir(previewDir,{recursive:true}); for(const s of ["표지","원천 자료","QoE 요약","운전자본","검토 후보","검토 흔적","검증"]){const blob=await wb.render({sheetName:s,autoCrop:"all",scale:1,format:"png"}); await fs.writeFile(`${previewDir}/${s.replaceAll(" ","_")}.png`,new Uint8Array(await blob.arrayBuffer()));}}
