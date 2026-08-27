// pdf.js is an ES module from v4 onwards -- the UMD build that set
// `window.pdfjsLib` from a plain <script> tag was removed, and v3.11.174 was
// the last release that had one. That is the only reason this project stayed
// on a version carrying CVE-2024-4367 (arbitrary JavaScript execution from a
// crafted font).
//
// So: import it here, once, and put it back on `window` under the name the
// console already uses. The rest of console.js is unchanged, and the API calls
// it makes -- getDocument({data}), .promise, GlobalWorkerOptions.workerSrc --
// are the same in v6 as they were in v3.
//
// `isEvalSupported: false` is not set here because it is a per-document
// option; console.js passes it at each getDocument call. Belt and braces: the
// font bug is fixed in this version, and the option closes the class of bug
// rather than the instance.
import * as pdfjsLib from "./pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = "vendor/pdf.worker.min.mjs";
window.pdfjsLib = pdfjsLib;
// A module script is deferred, so anything that ran before this point saw no
// pdfjsLib. Nothing in the console renders a PDF before a user asks for one,
// but the event is here so a future caller has something to wait for.
window.dispatchEvent(new CustomEvent("pdfjs-ready"));
