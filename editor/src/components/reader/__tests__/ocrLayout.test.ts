import { describe, expect, it } from 'vitest';
import { detectOcrSplit, mapOcrRegion, currentOcrPages } from '../lib/ocr-layout';
function scan(gutter: boolean) {
  const width=320,height=240,data=new Uint8ClampedArray(width*height*4).fill(255);
  for(let y=25;y<220;y++) for(let x=24;x<298;x++) {
    const text=y%12<4 && (!gutter || x<144 || x>180);
    const binding=gutter && x===162;
    if(text||binding){const i=(y*width+x)*4;data[i]=data[i+1]=data[i+2]=20;}
  }
  return {width,height,data};
}
describe('scanned spread layout',()=>{
  it('finds a central gutter despite a binding line',()=>{expect(detectOcrSplit(scan(true))).toBeGreaterThan(.45);expect(detectOcrSplit(scan(true))).toBeLessThan(.57);});
  it('does not split uninterrupted single-page lines or blank scans',()=>{expect(detectOcrSplit(scan(false))).toBeNull();expect(detectOcrSplit({width:320,height:240,data:new Uint8ClampedArray(320*240*4).fill(255)})).toBeNull();});
  it('keeps half-page OCR coordinates inside their original half',()=>{
    const span={left:.1,top:.2,width:.8,height:.03,text:'line',confidence:.7};
    const left=mapOcrRegion([span],0,.5)[0],right=mapOcrRegion([span],.5,.5)[0];
    expect(left.left+left.width).toBeLessThanOrEqual(.5);expect(right.left).toBeGreaterThanOrEqual(.5);expect(right.width).toBe(.4);expect(right.top).toBe(.2);
  });
  it('rejects legacy and other-layout caches while retaining matching split-page OCR',()=>{
    const legacy={page_number:1,spans:[]};
    const matching={page_number:2,spans:[],layout_version:3,layout_mode:'spread'};
    expect(currentOcrPages([legacy,matching,{...matching,page_number:3,layout_mode:'single'}],'spread')).toEqual([matching]);
    expect(currentOcrPages({},'auto')).toEqual([]);
  });
});
