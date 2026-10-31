"""Shared accessible web styling guided by Apple's Human Interface Guidelines."""
import re
PALETTES = {
    'light': {'bg':'#f5f5f7','panel':'#ffffff','surface':'#f2f2f7','control':'#ffffff',
              'line':'#d2d2d7','control-line':'#85858a','text':'#1d1d1f','muted':'#606067','accent':'#005bb5',
              'primary':'#0066cc','primary-hover':'#0055aa','on-primary':'#ffffff',
              'green':'#237537','red':'#b42324','warning':'#855000','hover':'#e8e8ed',
              'glass':'rgba(245,245,247,.88)','scrim':'rgba(0,0,0,.32)'},
    'dark': {'bg':'#000000','panel':'#1c1c1e','surface':'#2c2c2e','control':'#1c1c1e',
             'line':'#48484a','control-line':'#8e8e93','text':'#f5f5f7','muted':'#b5b5bc','accent':'#82b6ff',
             'primary':'#0066cc','primary-hover':'#0055aa','on-primary':'#ffffff',
             'green':'#76d98d','red':'#ff9999','warning':'#f0c675','hover':'#3a3a3c',
             'glass':'rgba(28,28,30,.88)','scrim':'rgba(0,0,0,.64)'},
}


def _tokens(palette):
    return ';'.join('--'+key+':'+value for key,value in palette.items())


DESIGN_CSS = ':root{'+_tokens(PALETTES['light'])+'}\n' + '@media(prefers-color-scheme:dark){:root:not([data-appearance="light"]){'+_tokens(PALETTES['dark'])+'}}\n' + ':root[data-appearance="light"]{'+_tokens(PALETTES['light'])+';color-scheme:light}\n:root[data-appearance="dark"]{'+_tokens(PALETTES['dark'])+';color-scheme:dark}\n' + '''
/* Semantic surfaces, system typography and an 8-point spacing rhythm. */
:root{color-scheme:light dark;--panel-padding:24px;--section-space:24px;--subsection-space:20px;--tile-gap:16px;--section-gap:24px;--field-gap:16px;--card-padding:20px;--control-gap:8px;font-size:106.25%;accent-color:var(--primary)}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%;scroll-padding-top:88px}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;font-size:1rem;line-height:1.5;letter-spacing:normal}
h1{font-family:inherit;font-size:clamp(1.5rem,3vw,2rem);font-weight:700;line-height:1.2;letter-spacing:-.025em}
h2{font-size:1.18rem;font-weight:600;line-height:1.35;letter-spacing:-.015em}
h3,.summary-subheading{font-size:1rem;font-weight:600;line-height:1.4;letter-spacing:normal}
p{line-height:1.55}a{color:var(--accent);text-underline-offset:3px}
.dashboard-page main{padding:32px max(20px,env(safe-area-inset-right)) max(48px,env(safe-area-inset-bottom)) max(20px,env(safe-area-inset-left))}
.top{align-items:center;gap:24px;margin-bottom:20px}.top-actions{gap:12px}
.panel{padding:var(--panel-padding);margin:20px 0;background:var(--panel);border:1px solid var(--line);border-radius:16px;box-shadow:none}
.panel.activity-panel{background:var(--surface);box-shadow:none}
.card,.option-card,.settings-card-grid>div:not(.option-card),.credential-editor{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px;box-shadow:none}
.card label,.field label,label{color:var(--muted);font-size:.82rem;font-weight:500;line-height:1.45}
.card label{margin-bottom:8px}.value{font-size:1.5rem;line-height:1.3;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.small,.hint,.field-description,.notice,.inline-status,.section-feedback{color:var(--muted);font-size:.76rem;line-height:1.55}
.small{margin-top:8px}.sub{color:var(--muted)}
/* One primary action; secondary actions stay visually quiet. */
button,.button,input,select{font-family:inherit;font-size:.94rem;line-height:1.35;min-height:44px;border-radius:10px}
input,select{background:var(--control);color:var(--text);border:1px solid var(--control-line);padding:10px 12px;max-width:100%;min-width:0}
input:focus,select:focus{border-color:var(--accent);box-shadow:none;outline:none}
input::placeholder{color:var(--muted);opacity:1}
button,.button{background:var(--primary);color:var(--on-primary);border:1px solid transparent;font-weight:600;padding:10px 16px;transition:background-color .15s ease,border-color .15s ease;box-shadow:none}
button.secondary,.button.secondary{background:var(--surface);color:var(--accent);border-color:var(--control-line)}
button:disabled,input:disabled,select:disabled{opacity:.55;cursor:not-allowed}
button:not(:disabled):hover,.button:hover{filter:none!important;transform:none!important;box-shadow:none!important}
@media(hover:hover) and (pointer:fine){
 button:not(:disabled):not(.secondary):hover,.button:not(.secondary):hover{background:var(--primary-hover)}
 button.secondary:not(:disabled):not(#previousPage):not(#nextPage):not(#goToPage):hover,.button.secondary:hover{background:var(--hover)}
}
button:not(:disabled):active,.button:active{filter:none!important;transform:none!important;box-shadow:none!important}
body :is(button,a,input,select,summary,[tabindex]):focus-visible{outline:3px solid var(--accent);outline-offset:3px}
.top-actions button,.top-actions .logout-button{font:600 .94rem/1.35 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;min-height:44px;padding:10px 16px;border-radius:10px}
.btc-balance-card #manualWithdraw{font-size:.82rem;padding:10px 14px;min-height:44px;border-radius:10px}
.controls input,.controls select,.controls>button{height:48px;min-height:48px;font-size:.94rem}
/* Disclosure headers are grouped surfaces rather than decorative banners. */
details.panel{border-radius:16px;overflow:hidden;isolation:isolate;background-clip:padding-box;border-color:var(--line)}
details.panel>.panel-heading,details.panel>.report-heading,details.panel:not([open])>summary{border-radius:0;box-shadow:none}
body details.panel>summary:focus-visible{outline-offset:-4px}
.panel>.panel-heading,.panel>.report-heading{margin:calc(-1 * var(--panel-padding)) calc(-1 * var(--panel-padding)) var(--panel-padding);padding:16px var(--panel-padding);background:var(--panel);border-bottom:1px solid var(--line);border-radius:15px 15px 0 0}
.panel>.panel-heading h2,.panel>.report-heading h2{font-size:1.18rem;font-weight:600;line-height:1.35}
summary{min-height:44px}details.panel>summary{min-height:56px}
details.panel>summary h2::after{content:'›';color:var(--muted);font-size:1.5rem;line-height:1;transform:none}
details.panel[open]>summary h2::after{content:'›';transform:rotate(90deg)}
details.panel:not([open])>summary{margin-bottom:calc(-1 * var(--panel-padding));border-radius:15px}
.report-controls>.sub{font-size:.88rem}.summary-subheading{margin-bottom:var(--subsection-space)}
/* Tables retain numeric alignment; status always has a text label. */
th{color:var(--muted);text-transform:none;letter-spacing:normal;font-size:.76rem;font-weight:600}
td{font-size:.88rem}th,td{border-color:var(--line)}#activities td{padding:8px 12px}#activities tr{height:40px}
.activity-status,.page-size,.page-jump,.pagination .notice{font-size:.76rem}
.pagination #previousPage,.pagination #nextPage,.pagination #goToPage{font:600 .82rem/1.35 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;min-height:44px;padding:10px 14px}
.page-size select,.page-jump input{min-height:44px}.pagination{gap:12px;margin-top:16px}
.positive,.status-confirmed,.withdraw-feedback.success{color:var(--green)}
.negative,.error,.status-failed,.section-feedback.is-error,.inline-status.is-error,#status.error,#passwordConfirmationError,.export-feedback{color:var(--red)}
.status-pending{color:var(--warning)}.status-muted{color:var(--muted)}
.feedback-close,.settings-dialog-header button{min-width:44px;min-height:44px;width:44px;padding:0;font-size:1.18rem;line-height:1;border-radius:10px}
.feedback-close{flex:0 0 44px;margin-top:0}.withdraw-action .feedback-close{margin-top:0}
/* Native dialogs provide keyboard containment, Escape and focus restoration. */
dialog,.confirmation-dialog{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:20px;box-shadow:0 24px 80px #0004;padding:24px;max-height:calc(100dvh - 32px)}
dialog::backdrop,.confirmation-dialog::backdrop{background:var(--scrim);backdrop-filter:none;-webkit-backdrop-filter:none}
dialog h2,.confirmation-dialog h2{font-size:1.18rem;font-weight:600}
.confirmation-values dt,dialog dt{font-size:.82rem;color:var(--muted)}
.confirmation-values dd,dialog dd{font-size:.94rem;color:var(--text)}
.confirmation-values,.confirmation-actions{gap:16px}.confirmation-actions{flex-wrap:wrap}
.settings-dialog{padding:0;overflow:hidden}.settings-dialog-header{background:var(--panel);border-color:var(--line);padding:12px 20px}
.settings-dialog iframe,.embedded,.embedded .panel{background:var(--panel)}
.review-list dt,.review-list dd{border-color:var(--line)}.review-list .review-changed{background:var(--surface);border-color:var(--accent)}
.review-list dt{font-size:.82rem}.review-new,.review-badge{color:var(--accent)}.review-old,.review-caption,.review-unchanged{color:var(--muted)}.review-caption,.review-badge{font-size:.76rem}
/* The complete option card is the switch's touch target. */
.option-card{align-items:center;gap:16px;min-height:100px;cursor:pointer}
.option-card:focus-within{outline:3px solid var(--accent);outline-offset:3px}.option-card input[type=checkbox]:focus-visible{outline:none}
.option-card strong,.currency-card label{font-size:.88rem;font-weight:600;color:var(--text)}
.option-card small{font-size:.76rem;line-height:1.5;color:var(--muted)}
.option-card input[type=checkbox]{flex:0 0 51px;width:51px;height:31px;min-height:31px;border:1px solid var(--line);border-radius:999px;background:var(--line)}
.option-card input[type=checkbox]::after{top:2px;left:2px;width:25px;height:25px;background:#fff;box-shadow:0 1px 3px #0003}
.option-card input[type=checkbox]:checked{background:#248a3d;border-color:#248a3d}
.option-card input[type=checkbox]:checked::after{transform:translateX(20px);background:#fff}
.currency-card{align-items:stretch;cursor:default}.credential-editor{background:var(--surface)}
.dashboard-footnotes{color:var(--muted);font-size:.76rem;line-height:1.6;margin-top:32px;padding-top:24px}
.dashboard-footnotes h2,.dashboard-footnotes li>strong,.dashboard-footnotes .note-subheading{font-size:.82rem;font-weight:600;letter-spacing:normal;color:var(--text)}
.dashboard-footnotes p{margin:8px 0 0}.note-subsection{margin-top:24px}
.horizontal-scroll{scrollbar-color:var(--muted) var(--surface);scrollbar-width:thin}
.horizontal-scroll::-webkit-scrollbar-track{background:var(--surface)}
.horizontal-scroll::-webkit-scrollbar-thumb{background:var(--muted);border-color:var(--surface)}
.horizontal-scroll::-webkit-scrollbar-thumb:hover,.horizontal-scroll::-webkit-scrollbar-thumb:active,.horizontal-scroll.is-scrolling::-webkit-scrollbar-thumb{background:var(--accent)}
.horizontal-scroll.has-overflow:hover,.horizontal-scroll.is-scrolling{scrollbar-color:var(--accent) var(--surface)}
.compact-brand-inner span{font-size:1rem;font-weight:600}.compact-brand-bar{background:var(--panel);color:var(--text);border-bottom:1px solid var(--line);box-shadow:none;backdrop-filter:none;-webkit-backdrop-filter:none}
@supports(backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px)){
 .compact-brand-bar{background:var(--glass);backdrop-filter:blur(20px) saturate(1.2);-webkit-backdrop-filter:blur(20px) saturate(1.2)}
}
.login-page .card{background:var(--panel);border-radius:16px;box-shadow:0 8px 32px #0001;padding:32px}
.login-brand{margin:0 0 24px;gap:16px}.login-brand h1{font-size:1.6rem}.login-page label{font-size:.82rem;margin-top:20px}
.login-page input{font-size:1rem;min-height:48px}.login-page button{margin-top:24px;min-height:48px}
.login-support,.login-support code{font-size:.7rem;line-height:1.6;color:var(--muted)}.login-support>span{color:var(--red)}
.login-page .error:empty{display:none}
.skip-link{position:fixed;top:12px;left:16px;z-index:1000;background:var(--panel);color:var(--accent);border:1px solid var(--line);border-radius:10px;padding:12px 16px;transform:translateY(-200%)}
.skip-link:focus{transform:none}
@media(max-width:600px){
 :root{--panel-padding:16px;--tile-gap:12px;--section-gap:20px;--card-padding:16px;--field-gap:16px}
 .dashboard-page main{padding-top:max(24px,env(safe-area-inset-top));padding-right:max(16px,env(safe-area-inset-right));padding-left:max(16px,env(safe-area-inset-left))}
 .panel{margin:16px 0}.panel>.panel-heading,.panel>.report-heading{padding:14px var(--panel-padding)}
 .card,.option-card,.settings-card-grid>div:not(.option-card),.credential-editor{padding:16px}
 .value{font-size:1.35rem}.top{gap:16px}.top-actions{gap:12px}.dashboard-brand h1{font-size:1.5rem}
 .settings-page main,.embedded main{padding:16px max(16px,env(safe-area-inset-right)) 32px max(16px,env(safe-area-inset-left))}
 .option-card{min-height:96px}input,select{font-size:1rem}
 .login-page{padding:max(16px,env(safe-area-inset-top)) 16px max(16px,env(safe-area-inset-bottom));min-height:100svh}
 .login-page .card{padding:24px}.login-brand{gap:12px}.login-brand h1{font-size:1.35rem}
 dialog,.confirmation-dialog{padding:20px;border-radius:20px}.settings-dialog{padding:0}
}
@media(prefers-contrast:more){
 :root,:root[data-appearance="light"]{--line:#767676;--control-line:#454545;--muted:#45454a;--accent:#004b96}
 .panel,.card,.option-card,input,select,button,.button{border-width:2px}
 .compact-brand-bar{background:var(--panel);backdrop-filter:none;-webkit-backdrop-filter:none}
}
@media(prefers-color-scheme:dark) and (prefers-contrast:more){:root:not([data-appearance="light"]){--line:#aeaeae;--control-line:#e0e0e6;--muted:#e0e0e6;--accent:#a6ccff}}
@media(prefers-contrast:more){:root[data-appearance="dark"]{--line:#aeaeae;--control-line:#e0e0e6;--muted:#e0e0e6;--accent:#a6ccff}}
@media(prefers-reduced-transparency:reduce){.compact-brand-bar{background:var(--panel);backdrop-filter:none;-webkit-backdrop-filter:none}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
@media(forced-colors:active){button,.button,input,select,.panel,.card,.option-card{border:1px solid ButtonText}.compact-brand-bar{background:Canvas;backdrop-filter:none;-webkit-backdrop-filter:none}}
'''



def apply_appearance(page, appearance='system'):
    """Choose the saved appearance before first paint, including public sign-in."""
    if appearance not in ('system', 'light', 'dark'):
        appearance = 'system'
    page = page.replace('<html lang="en">', '<html lang="en" data-appearance="'+appearance+'">', 1)
    if appearance != 'system':
        colors = iter([PALETTES[appearance]['bg']])
        def theme_color(match):
            color = next(colors, None)
            return '<meta name="theme-color" content="'+color+'">' if color else ''
        page = re.sub(r'<meta name="theme-color"[^>]*>', theme_color, page)
    return page
