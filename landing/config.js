// Demo-request form delivery — MUST be configured before publishing.
// See landing/README.md. Leave a value empty to disable that channel.
//
//   formEndpoint: an HTTPS endpoint that accepts a POST of form fields and
//                 answers 2xx (e.g. a Formspree form URL, or your own
//                 endpoint). Preferred: requests arrive without the visitor
//                 needing an e-mail client.
//   contactEmail: fallback — the visitor's e-mail client opens with the
//                 request pre-filled. Used only when formEndpoint is empty.
window.THRENDA_SITE_CONFIG = {
  formEndpoint: "",
  contactEmail: ""
};
