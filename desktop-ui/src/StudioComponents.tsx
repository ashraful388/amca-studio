import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { amca } from "./api";
import type { AssetRecord, LocalPathGrant } from "./types";
import { artifactFileName, codeFileExtension, saveFile } from "./outputModel";
import { canonicalSourceExtension, planArtifactPreview } from "./artifactPreviewModel";
import { Icon } from "./icons";
import { isDetailsLanguage, parseDetailsBlock } from "./markdownBlocks";

function CodeBlock({text, language}: {text:string; language:string}) {
  const [notice,setNotice]=useState("");
  return <section className="code-block"><header><span>{language || "text"}</span><button onClick={() => navigator.clipboard.writeText(text).then(() => setNotice("Copied"), () => setNotice("Copy unavailable"))}>{notice || "Copy"}</button><button onClick={() => void saveFile(`code.${codeFileExtension(language)}`, text).then((outcome) => setNotice(outcome.ok ? "Saved" : `Save failed: ${outcome.reason ?? "unknown error"}`))}>Save</button></header><pre><code>{text.split(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\b(?:const|let|var|function|return|if|else|for|while|class|import|from|def|True|False|null|None|async|await)\b|\b\d+(?:\.\d+)?\b)/g).map((part,i) => <span key={i} className={/^['"]/.test(part) ? "syntax-string" : /^(const|let|var|function|return|if|else|for|while|class|import|from|def|True|False|null|None|async|await)$/.test(part) ? "syntax-keyword" : /^\d/.test(part) ? "syntax-number" : undefined}>{part}</span>)}</code></pre></section>;
}
export function RichOutput({text,onLink}: {text:string;onLink?:(url:string)=>void}) {
  return <div className="markdown-message"><ReactMarkdown remarkPlugins={[remarkGfm,remarkMath]} rehypePlugins={[rehypeKatex]} components={{
    a: ({href,children}) => <a href={href} rel="noreferrer" onClick={onLink ? (event) => {event.preventDefault(); if(href) onLink(href);} : undefined}>{children}</a>,
    img: ({src,alt}) => <button className="inline-image" onClick={() => {if(src && onLink) onLink(src);}}><img src={src} alt={alt || "Generated image"} loading="lazy" /></button>,
    pre: ({children}) => <>{children}</>,
    code: ({children,className}) => {
      const language = className?.replace("language-","") || "text";
      const raw = String(children).replace(/\n$/,"");
      // A model may request a collapsible section.  The body is markdown
      // rendered by this same component, so no HTML or script can be injected.
      if (isDetailsLanguage(language)) {
        const block = parseDetailsBlock(raw);
        if (block) return <details className="md-details"><summary>{block.title}</summary><RichOutput text={block.body} onLink={onLink}/></details>;
      }
      return className || String(children).includes("\n") ? <CodeBlock text={raw} language={language} /> : <code>{children}</code>;
    }
  }}>{text}</ReactMarkdown></div>;
}
export function ResponseActions({id,text,onRetry,onSource,onDetails}: {id:string;text:string;onRetry:()=>void;onSource:()=>void;onDetails:()=>void}) {
 const [rating,setRating]=useState(() => localStorage.getItem(`amca.feedback.${id}`)||""); const [notice,setNotice]=useState("");
 const rate=(value:string)=>{const next=rating===value ? "":value;setRating(next);localStorage.setItem(`amca.feedback.${id}`,next);};
 // The four everyday actions are icons; everything else lives behind the
 // "more options" menu, so the row under a response stays quiet.
 return <div className="response-actions" aria-label="Response actions">
  <button className="response-action-icon" title="Copy response" aria-label="Copy response" onClick={() => navigator.clipboard.writeText(text).then(()=>setNotice("Copied"),()=>setNotice("Copy unavailable"))}><Icon name="copy"/></button>
  <button className="response-action-icon" aria-pressed={rating==="like"} title="Like" aria-label="Like" onClick={()=>rate("like")}><Icon name="thumbUp"/></button>
  <button className="response-action-icon" aria-pressed={rating==="dislike"} title="Dislike" aria-label="Dislike" onClick={()=>rate("dislike")}><Icon name="thumbDown"/></button>
  <button className="response-action-icon" title="Try Again" aria-label="Try Again" onClick={onRetry}><Icon name="refresh"/></button>
  <details className="response-actions-more"><summary title="More options" aria-label="More options"><Icon name="kebab"/></summary>
   <div className="response-actions-menu">
    <button onClick={onSource}>Add to Project Source</button>
    <button onClick={()=>void saveFile("AMCA-response.md",text).then((outcome)=>setNotice(outcome.ok?"Saved":"Save failed: "+(outcome.reason ?? "unknown error")))}>Save response</button>
    <button onClick={onDetails}>Run details</button>
   </div>
  </details>
  <small role="status">{notice}</small>
 </div>;
}
export function SourcePreview({asset}: {asset:AssetRecord}) {
 const [value,setValue]=useState<{url:string;text:string}|null>(null);const [error,setError]=useState("");
 useEffect(()=>{let cancelled=false;let url="";setValue(null);setError("");amca.assetContent(asset.project_id,asset.record_id).then(result=>{const bytes=Uint8Array.from(atob(result.data_base64),c=>c.charCodeAt(0));url=URL.createObjectURL(new Blob([bytes],{type:asset.media_type}));if(cancelled){URL.revokeObjectURL(url);return;}setValue({url,text:result.asset.chunks.map(chunk=>chunk.text || "").join("\n\n")});}).catch(error=>{if(!cancelled)setError(String(error));});return()=>{cancelled=true;if(url)URL.revokeObjectURL(url);};},[asset.project_id,asset.record_id]);
 return <section className="source-preview"><h2>{asset.filename}</h2><small>{asset.media_type} · {asset.size_bytes.toLocaleString()} bytes</small>{error&&<p role="alert">{error}</p>}{!value&&!error&&<p>Loading source…</p>}{value&&<><a className="secondary-button" href={value.url} download={asset.filename}>Download / Save</a>{asset.media_type.startsWith("image/") ? <a href={value.url} target="_blank" rel="noreferrer"><img src={value.url} alt={asset.filename}/></a> : asset.media_type==="application/pdf" ? <><p className="muted-copy">PDF text preview. Download for the original page layout.</p><RichOutput text={value.text || asset.extraction_note || "No extracted text is available for this PDF."}/></> : <><p className="muted-copy">Extracted document preview</p><RichOutput text={value.text || asset.extraction_note || "No text preview is available. Download to open in its native application."}/></>}</>}</section>;
}
export function BrowserInspector({projectId,grants,initialUrl}: {projectId:string;grants:LocalPathGrant[];initialUrl?:string}) {
 const [url,setUrl]=useState(initialUrl||"http://127.0.0.1");const [browser,setBrowser]=useState("edge");const [grant,setGrant]=useState("");const [approval,setApproval]=useState("");const [result,setResult]=useState<Record<string,unknown>|null>(null);const [error,setError]=useState("");const [busy,setBusy]=useState(false);
 useEffect(()=>{if(initialUrl)setUrl(initialUrl);},[initialUrl]);
 const act=async(action:string)=>{setBusy(true);setError("");try{if(action==="propose"){const r=await amca.browserProposal({project_id:projectId,grant_id:grant,browser,url,task:"Inspect this page",reason:"User requested page inspection"});setApproval(r.approval.id);setResult(r as unknown as Record<string,unknown>);}else if(action==="approve"||action==="deny"){await amca.decideApproval(approval,action==="approve");setResult({status:action==="approve"?"Approved. Open the governed browser to continue.":"Denied"});if(action==="deny")setApproval("");}else{setResult(await amca.browserExecute({project_id:projectId,grant_id:grant,approval_id:approval,operation:action}));if(action==="close")setApproval("");}}catch(e){setError(String(e));}finally{setBusy(false);}};
 return <section className="browser-inspector"><h2>Governed browser</h2><p>The runtime permits approved local pages. Internet URLs may be refused by current policy.</p><label>Page URL<input value={url} onChange={e=>{setUrl(e.target.value);setApproval("");}} /></label><label>Browser application<select value={browser} onChange={e=>{setBrowser(e.target.value);setApproval("");}}><option value="edge">Microsoft Edge</option><option value="chrome">Google Chrome</option><option value="firefox">Mozilla Firefox</option></select></label><label>Permission grant<select value={grant} onChange={e=>{setGrant(e.target.value);setApproval("");}}><option value="">Choose a grant</option>{grants.map(g=><option key={g.grant_id} value={g.grant_id}>{g.canonical_root}</option>)}</select></label><button disabled={busy||!projectId||!grant} onClick={()=>void act("propose")}>Request access</button>{approval&&<div className="response-actions">{["approve","deny","open","inspect","read","extract","close"].map(action=><button key={action} disabled={busy} onClick={()=>void act(action)}>{action}</button>)}</div>}{error&&<p role="alert">{error}</p>}{result&&<><p role="status">{String(result.status || "Permission request created")}</p>{result.dom_preview ? <pre>{String(result.dom_preview)}</pre> : null}{result.title ? <p><strong>{String(result.title)}</strong></p> : null}{result.text ? <><p className="muted-copy">Extracted page text{result.text_truncated ? " (truncated)" : ""}</p><pre className="browser-page-text">{String(result.text)}</pre></> : null}{Array.isArray(result.headings) && (result.headings as unknown[]).length ? <details><summary>Headings ({(result.headings as unknown[]).length})</summary><RecordedDetails value={result.headings}/></details> : null}{Array.isArray(result.links) && (result.links as unknown[]).length ? <details><summary>Links ({String(result.link_count ?? (result.links as unknown[]).length)})</summary><RecordedDetails value={result.links}/></details> : null}<details><summary>Provenance & exact scope</summary><RecordedDetails value={result}/></details></>}</section>;
}
export function SkillsWorkspace({onUse}: {onUse:(name:string,text:string)=>void}) {
 type Skill={name:string;text:string};const [skills,setSkills]=useState<Skill[]>(()=>{try{return JSON.parse(localStorage.getItem("amca.skills")||"[]");}catch{return [];}});const [name,setName]=useState("");const [text,setText]=useState("");
 const save=(items:Skill[])=>{setSkills(items);localStorage.setItem("amca.skills",JSON.stringify(items));};
 return <section className="skills-workspace"><h1>Skills</h1><p>Reusable instructions saved on this device. Invoking a skill adds its instructions to the message for review.</p>{skills.map((skill,index)=><article className="file-row" key={index}><strong>{skill.name}</strong><button onClick={()=>onUse(skill.name,skill.text)}>Use skill</button><button onClick={()=>{setName(skill.name);setText(skill.text);}}>Edit</button><button onClick={()=>{if(window.confirm(`Delete skill ${skill.name}?`))save(skills.filter((_,i)=>i!==index));}}>Delete</button></article>)}<form onSubmit={e=>{e.preventDefault();save([...skills.filter(s=>s.name!==name.trim()),{name:name.trim(),text}]);setName("");setText("");}}><label>Name<input required value={name} onChange={e=>setName(e.target.value)} /></label><label>Instructions<textarea required value={text} onChange={e=>setText(e.target.value)} rows={6}/></label><button disabled={!name.trim()||!text.trim()}>Save skill</button></form></section>;
}

export function RecordedDetails({value}: {value:unknown}) {
 if(value===null||value===undefined)return <span>Not recorded</span>;
 if(typeof value!=="object")return <span>{String(value)}</span>;
 if(Array.isArray(value))return value.length ? <ul>{value.map((item,i)=><li key={i}><RecordedDetails value={item}/></li>)}</ul>:<span>No recorded items</span>;
 return <dl className="recorded-details">{Object.entries(value as Record<string,unknown>).map(([key,item])=><div key={key}><dt>{key.replaceAll("_"," ")}</dt><dd><RecordedDetails value={item}/></dd></div>)}</dl>;
}
export function ResearchWorkspace({projectId,onInspect}: {projectId:string;onInspect:(title:string,value:unknown)=>void}) {
 const [question,setQuestion]=useState("");const [runs,setRuns]=useState<Array<Record<string,unknown>>>([]);const [selected,setSelected]=useState("");const [status,setStatus]=useState<Record<string,unknown>>({});const [result,setResult]=useState<Record<string,unknown>>({});const [error,setError]=useState("");const [busy,setBusy]=useState(false);
 const refresh=async()=>{try{setRuns((await amca.researchRuns(projectId)).runs);}catch(e){setError(String(e));}};
 useEffect(()=>{setRuns([]);setSelected("");setStatus({});setResult({});void refresh();},[projectId]);
 useEffect(()=>{if(!selected)return;let cancelled=false;const poll=async()=>{try{const data=await amca.researchDetail(projectId,selected);if(!cancelled)setStatus(data);}catch(e){if(!cancelled)setError(String(e));}};void poll();const timer=setInterval(poll,3000);return()=>{cancelled=true;clearInterval(timer);};},[selected,projectId]);
 const action=async(action:string)=>{setBusy(true);setError("");try{let id=selected;if(action==="create"){id=(await amca.createResearchRun(projectId,question)).run_id;setSelected(id);await amca.researchAction(projectId,id,"execute");}else await amca.researchAction(projectId,id,action);setStatus(await amca.researchDetail(projectId,id));await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}};
 const read=async(section:string,label:string)=>{try{const data=await amca.researchDetail(projectId,selected,section);if(section==="synthesis")setResult(data);else onInspect(label,data);}catch(e){setError(String(e));}};
 return <section className="research-workspace"><h1>Research</h1><p>Question → Sources → Extraction → Evidence → Claims & conflicts → Synthesis → Citations</p><p className="muted-copy">Project-scoped, bounded research using the available runtime.</p><form onSubmit={e=>{e.preventDefault();void action("create");}}><label>Research question<textarea value={question} onChange={e=>setQuestion(e.target.value)} rows={3} required/></label><button className="primary-button" disabled={busy||!question.trim()||!projectId}>Start research</button></form>{error&&<p role="alert">{error}</p>}
 <div className="file-row"><label>Research tasks<select value={selected} onChange={e=>setSelected(e.target.value)}><option value="">Select a task</option>{runs.map(run=><option key={String(run.run_id)} value={String(run.run_id)}>{String(run.objective||run.run_id)}</option>)}</select></label><button onClick={()=>void refresh()}>Refresh</button></div>
 {selected&&<><p role="status">Status: {String(status.status||"Loading")}</p><nav className="response-actions"><button onClick={()=>void read("planning","Research plan")}>Plan</button><button onClick={()=>void read("provenance","Sources & citations")}>Sources & citations</button><button onClick={()=>void read("trail","Evidence, claims & conflicts")}>Evidence & claims</button><button onClick={()=>void read("synthesis","Synthesis")}>Synthesis</button><button disabled={busy} onClick={()=>void action("cancel")}>Cancel</button><button disabled={busy} onClick={()=>void action("resume")}>Resume</button><button disabled={busy} onClick={()=>void action("execute")}>Execute planned task</button></nav><h2>Synthesis</h2>{Array.isArray(result.plans) && result.plans.length ? result.plans.map((plan,index)=>{const entry=plan as {version?:{sections?:unknown;grounding_status?:string}};return <article key={index}><p>{entry.version?.grounding_status || "No synthesis version yet"}</p><RecordedDetails value={entry.version?.sections}/></article>;}) : <p>No synthesis recorded. Run research, then open Synthesis to inspect available findings.</p>}</>}
 </section>;
}
/**
 * Present an artifact's canonical source in the form a reader can use.
 *
 * A spreadsheet is shown as a table, a document as rendered markdown, JSON
 * pretty-printed — and when no meaningful preview is possible the reason is
 * stated rather than raw bytes being passed off as a preview.
 */
export function ArtifactPreview({artifactType, content}: {artifactType:string; content:string}) {
  const plan = planArtifactPreview(artifactType, content);
  return <section className="artifact-preview" data-preview-kind={plan.kind}>
    <header><strong>{plan.label}</strong>{plan.note && <small>{plan.note}</small>}</header>
    {plan.kind === "table" && plan.table && <div className="artifact-table-wrap"><table className="artifact-table"><thead><tr>{plan.table.headers.map((header, index) => <th key={index}>{header}</th>)}</tr></thead><tbody>{plan.table.rows.map((row, rowIndex) => <tr key={rowIndex}>{plan.table!.headers.map((_, cellIndex) => <td key={cellIndex}>{row[cellIndex] ?? ""}</td>)}</tr>)}</tbody></table></div>}
    {plan.kind === "json" && <pre className="artifact-json">{plan.json}</pre>}
    {plan.kind === "slides" && plan.slides && <ol className="artifact-slides">{plan.slides.map((slide, index) => <li key={index}><strong>{slide.title}</strong>{slide.lines.length > 0 && <ul>{slide.lines.map((line, lineIndex) => <li key={lineIndex}>{line}</li>)}</ul>}</li>)}</ol>}
    {plan.kind === "image" && plan.imageUrl && <img className="artifact-image" src={plan.imageUrl} alt="Artifact preview" />}
    {plan.kind === "html" && <iframe className="artifact-html-preview" title="Artifact HTML preview" sandbox="" srcDoc={plan.source ?? ""} />}
    {plan.kind === "markdown" && <RichOutput text={plan.source ?? ""} />}
    {plan.kind === "source" && <pre className="artifact-source">{plan.source}</pre>}
    {plan.kind === "unavailable" && plan.source && <details><summary>Show stored source</summary><pre className="artifact-source">{plan.source}</pre></details>}
  </section>;
}
export function ArtifactInspector({projectId,relatedProjectIds=[],focusArtifactId,onSource}: {projectId:string;relatedProjectIds?:string[];focusArtifactId?:string;onSource?:(text:string)=>void}) {
 const scopeKey=[...new Set([projectId,...relatedProjectIds])].sort().join("|");
 const [items,setItems]=useState<import("./types").ArtifactMeta[]>([]);const [selected,setSelected]=useState("");const [source,setSource]=useState<import("./types").ArtifactSourceDoc|null>(null);const [draft,setDraft]=useState("");const [editing,setEditing]=useState(false);const [error,setError]=useState("");
 const [renditions,setRenditions]=useState<import("./types").ArtifactRenderCapability[]>([]);
 useEffect(()=>{let cancelled=false;amca.artifactRenderCapabilities().then(r=>{if(!cancelled)setRenditions(r.capabilities);}).catch(()=>{if(!cancelled)setRenditions([]);});return()=>{cancelled=true;};},[]);
 const artifactProject=items.find(item=>item.artifact_id===selected)?.project_id || projectId;
 const refresh=()=>Promise.all(scopeKey.split("|").map(id=>amca.artifactsList(id))).then(results=>setItems(results.flatMap(r=>r.artifacts))).catch(e=>setError(String(e)));
 useEffect(()=>{setItems([]);setSelected("");setSource(null);void refresh();},[scopeKey]);
 useEffect(()=>{if(!focusArtifactId)return;if(items.some(item=>item.artifact_id===focusArtifactId))setSelected(focusArtifactId);},[focusArtifactId,items]);
 useEffect(()=>{if(!selected)return;let cancelled=false;setError("");amca.artifactSource(artifactProject,selected).then(r=>{if(!cancelled){setSource(r.source);setDraft(r.source.content);}}).catch(e=>setError(String(e)));return()=>{cancelled=true;};},[selected,artifactProject]);
 const selectedMeta=items.find(item=>item.artifact_id===selected);const selectedType=selectedMeta?.artifact_type??"";const selectedTitle=selectedMeta?.title??"";const exportFile=async(format:string)=>{try{const r=await amca.artifactExport(artifactProject,selected,format);if(r.export.data_base64){const outcome=await saveFile(artifactFileName(selectedTitle,selectedType,format),new Blob([Uint8Array.from(atob(r.export.data_base64),c=>c.charCodeAt(0))]));if(!outcome.ok)setError(`Could not save the file: ${outcome.reason ?? "unknown error"}`);}else setError(r.export.reason||"Export is unavailable in this runtime.");}catch(e){setError(String(e));}};
 return <section><h2>Artifacts</h2>{error&&<p role="alert">{error}</p>}{items.map(item=><article className="file-row" key={item.artifact_id}><div><strong>{item.title}</strong><small>{item.artifact_type} · v{item.current_version}</small></div><button onClick={()=>{setSelected(item.artifact_id);setEditing(false);}}>Open</button></article>)}{!items.length&&<p>No artifacts recorded in this task.</p>}{source&&<><nav className="response-actions"><button onClick={()=>setEditing(!editing)}>Edit</button><button onClick={()=>void saveFile(artifactFileName(selectedTitle,selectedType,canonicalSourceExtension(selectedType)),source.content).then((outcome)=>{if(!outcome.ok)setError(`Could not save the file: ${outcome.reason ?? "unknown error"}`);})}>Save source</button>{onSource&&<button onClick={()=>onSource(source.content)}>Add to Project Source</button>}{selectedType==="bib"&&<button onClick={()=>{const name=`${(selectedTitle||"Bibliography").replace(/[<>:"/\|?*]/g,"_").replace(/\.bib$/i,"")}.bib`;saveFile(name,new Blob([source.content],{type:"text/x-bibtex"}));}}>Download .bib</button>}{["docx","html","zip","xlsx","pdf"].map(format=>{const cap=renditions.find(r=>r.artifact_type===selectedType&&r.target_format===format);const blocked=cap?!cap.available:(format==="pdf"||format==="docx"||format==="xlsx"?renditions.length>0:false);return <button key={format} title={blocked&&cap?cap.reason:undefined} aria-disabled={blocked} onClick={()=>void exportFile(format)}>Download {format}{blocked?" (unavailable)":""}</button>;})}
{renditions.some(r=>!r.available)&&<small className="muted-copy">Some formats are unavailable in this build: {renditions.filter(r=>!r.available).map(r=>r.target_format).join(", ")}.</small>}</nav>{editing?<><textarea aria-label="Artifact content" value={draft} onChange={e=>setDraft(e.target.value)} rows={12}/><button onClick={async()=>{try{await amca.artifactEdit(artifactProject,selected,draft,source.version);const r=await amca.artifactSource(artifactProject,selected);setSource(r.source);setEditing(false);await refresh();}catch(e){setError(String(e));}}}>Save new version</button></>:<ArtifactPreview artifactType={selectedType} content={source.content}/>}<details><summary>Versions & provenance</summary><RecordedDetails value={source.provenance}/>{Array.from({length:source.version-1},(_,i)=>i+1).map(version=><button key={version} onClick={async()=>{try{await amca.artifactRestore(artifactProject,selected,version);setSource((await amca.artifactSource(artifactProject,selected)).source);await refresh();}catch(e){setError(String(e));}}}>Restore v{version}</button>)}</details></>}</section>;
}

/**
 * Confirm deleting a project.
 *
 * States plainly what is removed and what is not, because "delete project" is
 * ambiguous in every product that has projects: it is not obvious whether the
 * chats inside it survive.
 */
export function ProjectDeleteDialog({project,chatCount,busy,onCancel,onConfirm}: {project:{name:string};chatCount:number;busy:boolean;onCancel:()=>void;onConfirm:()=>void}) {
 const dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{dialog.current?.showModal();},[]);
 return <dialog ref={dialog} className="thread-dialog" aria-labelledby="project-delete-title" onCancel={event=>{event.preventDefault();if(!busy)onCancel();}}>
  <form onSubmit={event=>{event.preventDefault();onConfirm();}}>
   <h2 id="project-delete-title">Delete project “{project.name}”?</h2>
   <p>This removes the project{chatCount > 0 ? <> and its <strong>{chatCount}</strong> chat{chatCount === 1 ? "" : "s"}</> : null} from this device, along with its files, instructions and settings.</p>
   <p>Generated artifacts and files already saved to disk are not touched. This cannot be undone.</p>
   <div className="response-actions"><button type="button" disabled={busy} onClick={onCancel}>Cancel</button><button className="danger-button" disabled={busy}>{busy ? "Deleting…" : "Delete project"}</button></div>
  </form>
 </dialog>;
}

export function ThreadDialog({action,title,onConfirm,onClose}: {action:"rename"|"delete";title:string;onConfirm:(title:string)=>Promise<void>;onClose:()=>void}) {
 const dialog=useRef<HTMLDialogElement>(null);const [value,setValue]=useState(title);const [busy,setBusy]=useState(false);
 useEffect(()=>{dialog.current?.showModal();},[]);
 return <dialog ref={dialog} className="thread-dialog" aria-labelledby="thread-dialog-title" onCancel={event=>{event.preventDefault();if(!busy)onClose();}}><form onSubmit={async event=>{event.preventDefault();setBusy(true);try{await onConfirm(value.trim());onClose();}finally{setBusy(false);}}}><h2 id="thread-dialog-title">{action==="rename"?"Rename chat":"Delete chat?"}</h2>{action==="rename"?<label>Chat name<input autoFocus required value={value} onChange={event=>setValue(event.target.value)}/></label>:<p>Delete “{title}” and its saved messages? This cannot be undone.</p>}<div className="response-actions"><button type="button" disabled={busy} onClick={onClose}>Cancel</button><button disabled={busy||(action==="rename"&&!value.trim())}>{action==="rename"?"Save name":"Delete chat"}</button></div></form></dialog>;
}
