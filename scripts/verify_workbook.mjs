import fs from 'node:fs/promises';
import {FileBlob, SpreadsheetFile} from '@oai/artifact-tool';

const [input, previewDir] = process.argv.slice(2);
if (!input || !previewDir) throw new Error('사용법: node scripts/verify_workbook.mjs 파일.xlsx 미리보기폴더');
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(input));
wb.recalculate();
for (const range of ["'정상화 손익'!A4:D14", "'QoE 요약'!A4:D18", "'검증'!A10:G12"]){
  console.log((await wb.inspect({kind:'table',range,include:'values,formulas',tableMaxRows:16,tableMaxCols:8})).ndjson);
}
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:30},summary:'release formula scan'})).ndjson);
await fs.mkdir(previewDir,{recursive:true});
for (const name of ['표지','원천 자료','QoE 요약','정상화 손익','운전자본','순차입금','검토 후보','검토 흔적','검증']){
  const png=await wb.render({sheetName:name,autoCrop:'all',scale:1,format:'png'});
  await fs.writeFile(`${previewDir}/${name.replaceAll(' ','_')}.png`,new Uint8Array(await png.arrayBuffer()));
}
const candidates=wb.worksheets.getItem('검토 후보');
candidates.getRange('C3').values=[['아니요']];
wb.recalculate();
const normalized=wb.worksheets.getItem('정상화 손익');
const result=normalized.getRange('D5:D9').values;
if(result[0][0]!==result[4][0])throw new Error('전체 조정 제외 시 보고 영업이익과 일치하지 않습니다.');
console.log(JSON.stringify({overrideCheck:'passed',reported:result[0][0],normalized:result[4][0]}));
