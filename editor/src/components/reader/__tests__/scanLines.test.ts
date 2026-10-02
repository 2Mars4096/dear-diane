import { expect, it } from 'vitest';
import { scanLines } from '../lib/scan-lines';
import { parseTesseractTsv, shouldOcrPage } from '../lib/ocr';

it('keeps a visual line selectable when recognition omits it without stretching recognized words', () => {
  const width=300,height=200,data=new Uint8ClampedArray(width*height*4).fill(255);
  for (const top of [30,65,100]) for(let y=top;y<top+12;y++) for(let x=20;x<250;x++) {const i=(y*width+x)*4;data[i]=data[i+1]=data[i+2]=0;}
  const known={left:20/width,top:30/height,width:30/width,height:12/height,confidence:.9,text:'文本'};
  const spans=scanLines({width,height,data},[known]);
  expect(spans[0]).toEqual(known);
  expect(spans.filter(s=>s.geometryOnly)).toHaveLength(2);
  expect(spans.map(s=>s.top)).toEqual([.15,.325,.5]);
});

it('keeps individual Chinese OCR boxes at their actual positions', () => {
  const tsv='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t\n5\t1\t1\t1\t1\t1\t10\t10\t10\t10\t90\t中\n5\t1\t1\t1\t1\t2\t25\t10\t10\t10\t90\t文';
  expect(parseTesseractTsv(tsv,'word').spans.map(s=>[s.text,s.left,s.width])).toEqual([['中',.1,.1],['文',.25,.1]]);
  expect(shouldOcrPage('中文文本')).toBe(false);
  expect(shouldOcrPage('')).toBe(true);
});
