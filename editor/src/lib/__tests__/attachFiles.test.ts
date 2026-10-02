// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest';
import { nativeFs } from '../electronBridge';
import { prepareAttachments, transferFiles } from '../attachFiles';
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();document.querySelector('meta[name="dan-remote-machine"]')?.remove();});
it('accepts PDFs and text as well as images from clipboard files',()=>{
 const pdf=new File(['pdf'],'paper.pdf',{type:'application/pdf'});
 expect(transferFiles({files:[pdf],items:[]} as unknown as DataTransfer)).toEqual([pdf]);
});
it('preserves native files as references and classifies non-images correctly',async()=>{
 vi.spyOn(nativeFs,'droppedFile').mockResolvedValue('/project/report.pdf'); const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 const result=await prepareAttachments([new File(['pdf'],'report.pdf',{type:'application/pdf'})],'picker');
 expect(result.errors).toEqual([]);expect(result.attachments[0]).toMatchObject({kind:'file',name:'report.pdf',path:'/project/report.pdf'});expect(fetcher).not.toHaveBeenCalled();
});
it('uploads browser files to the execution host and keeps successful files when one fails',async()=>{
 const meta=document.createElement('meta');meta.name='dan-remote-machine';document.head.append(meta);
 const native=vi.spyOn(nativeFs,'droppedFile');const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({path:'/host/notes.txt'}))).mockResolvedValueOnce(new Response('',{status:413}));vi.stubGlobal('fetch',fetcher);
 const files=[new File(['notes'],'notes.txt'),new File(['bad'],'bad.zip')];const result=await prepareAttachments(files,'drop');
 expect(native).not.toHaveBeenCalled();expect(fetcher.mock.calls[0][1].body).toBe(files[0]);expect(result.attachments[0].path).toBe('/host/notes.txt');expect(result.errors[0]).toContain('bad.zip');
});
