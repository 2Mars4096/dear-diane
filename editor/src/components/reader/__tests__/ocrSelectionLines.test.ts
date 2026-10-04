import { expect, it } from 'vitest';
import { ocrSelectionLines, refineOcrLines } from '../lib/ocr-selection-lines';
import type { PdfOcrTextSpan } from '../lib/pdf-ocr';
const word = (left: number, top: number, text = '文字', width = .07): PdfOcrTextSpan => ({ left, top, width, height: .03, text, confidence: .9 });

it('joins skewed words into continuous rows and excludes detached binding fragments', () => {
  const spans = [word(.2,.2), word(.28,.203), word(.36,.201), word(.03,.2,'|',.01),
    word(.2,.25), word(.28,.252), { ...word(.03,.2,'|',.01), height:.4 }];
  const lines = ocrSelectionLines(spans,null,1);
  expect(lines).toHaveLength(2);
  expect(lines[0].left).toBe(.2);
  expect(lines[0].width).toBeCloseTo(.23);
  expect(lines[0].text).not.toContain('|');
  expect(spans).toHaveLength(7); // Existing cached geometry is untouched.
});

it('keeps each printed page in reading order without joining across the gutter', () => {
  const spans = [word(.1,.2),word(.18,.2),word(.1,.25),word(.65,.2),word(.73,.2),word(.65,.25)];
  const lines=ocrSelectionLines(spans,.5,1.5);
  expect(lines.map(line=>line.left)).toEqual([.1,.1,.65,.65]);
  expect(lines.slice(0,2).every(line=>line.left+line.width<.5)).toBe(true);
});

it('fills word-recognition gaps from pixels while excluding scan margins', () => {
  const width=300,height=300,data=new Uint8ClampedArray(width*height*4).fill(255);
  const paint=(left:number,top:number,right:number,bottom:number)=>{
    for(let y=top;y<bottom;y++)for(let x=left;x<right;x++){const i=(y*width+x)*4;data[i]=data[i+1]=data[i+2]=0;}
  };
  for(const top of [50,80,110,140])paint(60,top,250,top+10);
  paint(8,0,13,height);
  const known=[50,80,140].map(top=>({...word(.2,top/height,'recognized words',.63),height:10/height}));
  const lines=refineOcrLines({width,height,data},known,null);
  expect(lines).toHaveLength(4);
  expect(lines.every(line=>line.left===.2 && Math.abs(line.width-190/300)<.0001)).toBe(true);
  expect(lines[2].geometryOnly).toBe(true);
  expect(lines[3].text).toBe('recognized words');
});

it('retains sparse page lines when pixel evidence cannot establish a text block',()=>{
  const lines=[word(.2,.2,'Short caption')];
  expect(refineOcrLines({width:20,height:20,data:new Uint8ClampedArray(1600).fill(255)},lines,null)).toEqual(lines);
});
