# Source: https://0.343700.xyz/posts/2024/12%E6%9C%88/1219av1-svt/
# Fetched: 2026-08-29 17:08:54 +08:00

<!DOCTYPE html><html class="scroll-smooth" lang="zh-CN"> <head><meta charset="utf-8"><meta content="width=device-width, initial-scale=1.0" name="viewport"><title>SVT-AV1 编码指南 • 三七の小站</title><link href="/icons/site.avif" rel="icon" type="image/avif"><link rel="icon" href="/favicon-32x32.png" type="image/png"><link href="/icons/apple-touch-icon.png" rel="apple-touch-icon"><link href="https://0.343700.xyz/posts/2024/12%E6%9C%88/1219av1-svt/" rel="canonical"><meta content="SVT-AV1 编码指南 • 三七の小站" name="title"><meta content="SVT-AV1 编码指南" name="description"><meta content="三七" name="author"><meta content="" name="theme-color"><meta content="article" property="og:type"><meta content="SVT-AV1 编码指南" property="og:title"><meta content="SVT-AV1 编码指南" property="og:description"><meta content="https://0.343700.xyz/posts/2024/12%E6%9C%88/1219av1-svt/" property="og:url"><meta content="三七の小站" property="og:site_name"><meta content="zh-CN" property="og:locale"><meta content="https://0.343700.xyz/social-card.avif" property="og:image"><meta content="1200" property="og:image:width"><meta content="630" property="og:image:height"><meta content="三七" property="article:author"><meta content="2024-12-19T00:00:00.000Z" property="article:published_time"><meta content="summary_large_image" property="twitter:card"><meta content="https://0.343700.xyz/posts/2024/12%E6%9C%88/1219av1-svt/" property="twitter:url"><meta content="SVT-AV1 编码指南" property="twitter:title"><meta content="SVT-AV1 编码指南" property="twitter:description"><meta content="https://0.343700.xyz/social-card.avif" property="twitter:image"><link href="/sitemap-index.xml" rel="sitemap"><link href="/rss.xml" rel="alternate" title="三七の小站" type="application/rss+xml"><meta content="Astro v5.0.5" name="generator"><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.15.3/dist/katex.min.css"><style>[data-astro-image]{aspect-ratio:var(--w) /var(--h);height:auto;-o-object-fit:var(--fit);object-fit:var(--fit);-o-object-position:var(--pos);object-position:var(--pos);width:100%}[data-astro-image=responsive]{max-height:calc(var(--h)*1px);max-width:calc(var(--w)*1px)}[data-astro-image=fixed]{height:calc(var(--h)*1px);width:calc(var(--w)*1px)}
</style>
<link rel="stylesheet" href="/_astro/_slug_.D1piAFXK.css"><script type="module" src="/_astro/page.DCTgdEH6.js"></script></head> <!-- 修改 max-w-6xl 最大宽度--> <body class="mx-auto flex min-h-screen max-w-4xl flex-col bg-bgColor px-4 pt-16 font-mono text-sm font-normal text-textColor antialiased sm:px-8"> <script>
	const lightModePref = window.matchMedia("(prefers-color-scheme: light)");

	function getUserPref() {
		const storedTheme = typeof localStorage !== "undefined" && localStorage.getItem("theme");
		return storedTheme || (lightModePref.matches ? "light" : "dark");
	}

	function setTheme(newTheme) {
		if (newTheme !== "light" && newTheme !== "dark") {
			return console.warn(
				`Invalid theme value '${newTheme}' received. Expected 'light' or 'dark'.`,
			);
		}

		const root = document.documentElement;

		// root already set to newTheme, exit early
		if (newTheme === root.getAttribute("data-theme")) {
			return;
		}

		root.setAttribute("data-theme", newTheme);

		const colorThemeMetaTag = document.querySelector("meta[name='theme-color']");
		const bgColour = getComputedStyle(document.body).getPropertyValue("--theme-bg");
		colorThemeMetaTag.setAttribute("content", `hsl(${bgColour})`);
		if (typeof localStorage !== "undefined") {
			localStorage.setItem("theme", newTheme);
		}
	}

	// initial setup
	setTheme(getUserPref());

	// View Transitions hook to restore theme
	document.addEventListener("astro:after-swap", () => setTheme(getUserPref()));

	// listen for theme-change custom event, fired in src/components/ThemeToggle.astro
	document.addEventListener("theme-change", (e) => {
		setTheme(e.detail.theme);
	});

	// listen for prefers-color-scheme change.
	lightModePref.addEventListener("change", (e) => setTheme(e.matches ? "light" : "dark"));
</script> <a class="sr-only focus:not-sr-only focus:fixed focus:start-1 focus:top-1.5" href="#main">skip to content
</a> <header class="group relative mb-16 flex items-center sm:ps-[4.5rem]" id="main-header"> <div class="flex sm:flex-col"> <!-- 修改：grayscale hover:filter-none 平时黑白，选中时恢复 ，改为 hover:contrast-50 --> <a aria-current="false" class="inline-flex items-center hover:contrast-50 sm:relative sm:inline-block" href="/"> <!-- 修改：加入自己的图片 --> <img src="/logo256.avif" alt="Logo" class="me-3 h-10 w-10 sm:absolute sm:start-[-5rem] sm:top-[-0.4rem] sm:me-0 sm:h-16 sm:w-16"> <!-- <svg
				aria-hidden="true"
				class="me-3 h-10 w-6 sm:absolute sm:start-[-4.5rem] sm:me-0 sm:h-20 sm:w-12"
				fill="none"
				focusable="false"
				viewBox="0 0 272 480"
				xmlns="http://www.w3.org/2000/svg"
			>
				<title>Logo</title>
				<path
					d="M181.334 93.333v-40L226.667 80v40l-45.333-26.667ZM136.001 53.333 90.667 26.667v426.666L136.001 480V53.333Z"
					fill="#B04304"></path>
				<path
					d="m136.001 119.944 45.333-26.667 45.333 26.667-45.333 26.667-45.333-26.667ZM90.667 26.667 136.001 0l45.333 26.667-45.333 26.666-45.334-26.666ZM181.334 53.277l45.333-26.666L272 53.277l-45.333 26.667-45.333-26.667ZM0 213.277l45.333-26.667 45.334 26.667-45.334 26.667L0 213.277ZM136 239.944l-45.333-26.667v53.333L136 239.944Z"
					fill="#FF5D01"></path>
				<path
					d="m136 53.333 45.333-26.666v120L226.667 120V80L272 53.333V160l-90.667 53.333v240L136 480V306.667L45.334 360V240l45.333-26.667v53.334L136 240V53.333Z"
					fill="#53C68C"></path>
				<path d="M45.334 240 0 213.334v120L45.334 360V240Z" fill="#B04304"></path>
			</svg> --> <!-- 修改：首页标题 --> <span class="text-xl font-bold sm:text-2xl">三七の小站</span> </a> <nav aria-label="Main menu" class="absolute -inset-x-4 top-14 hidden flex-col items-end gap-y-4 rounded-md bg-bgColor/[.85] py-4 text-accent shadow backdrop-blur group-[.menu-open]:z-50 group-[.menu-open]:flex sm:static sm:z-auto sm:-ms-4 sm:mt-1 sm:flex sm:flex-row sm:items-center sm:divide-x sm:divide-accent sm:rounded-none sm:bg-transparent sm:py-0 sm:shadow-none sm:backdrop-blur-none" id="navigation-menu"> <a aria-current="false" class="px-4 py-4 underline-offset-2 sm:py-0 sm:hover:underline" data-astro-prefetch href="/"> 主页 </a><a aria-current="false" class="px-4 py-4 underline-offset-2 sm:py-0 sm:hover:underline" data-astro-prefetch href="/about/"> 关于 </a><a aria-current="false" class="px-4 py-4 underline-offset-2 sm:py-0 sm:hover:underline" data-astro-prefetch href="/posts/"> 随笔 </a><a aria-current="false" class="px-4 py-4 underline-offset-2 sm:py-0 sm:hover:underline" data-astro-prefetch href="/notes/"> 札记 </a> </nav> </div> <site-search class="ms-auto" id="search" data-astro-cid-otpdt6jm="true"> <button class="flex h-9 w-9 items-center justify-center rounded-md hover:text-accent" data-open-modal disabled data-astro-cid-otpdt6jm> <svg aria-label="search" class="h-7 w-7" fill="none" height="16" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" viewBox="0 0 24 24" width="16" xmlns="http://www.w3.org/2000/svg" data-astro-cid-otpdt6jm> <path d="M0 0h24v24H0z" stroke="none" data-astro-cid-otpdt6jm></path> <path d="M3 10a7 7 0 1 0 14 0 7 7 0 1 0-14 0M21 21l-6-6" data-astro-cid-otpdt6jm></path> </svg> </button> <dialog aria-label="search" class="h-full max-h-full w-full max-w-full border border-zinc-400 bg-bgColor shadow backdrop:backdrop-blur sm:mx-auto sm:mb-auto sm:mt-16 sm:h-max sm:max-h-[calc(100%-8rem)] sm:min-h-[15rem] sm:w-5/6 sm:max-w-[48rem] sm:rounded-md" data-astro-cid-otpdt6jm> <div class="dialog-frame flex flex-col gap-4 p-6 pt-12 sm:pt-6" data-astro-cid-otpdt6jm> <button class="ms-auto cursor-pointer rounded-md bg-zinc-200 p-2 font-semibold dark:bg-zinc-700" data-close-modal data-astro-cid-otpdt6jm>Close</button> <div class="search-container" data-astro-cid-otpdt6jm> <div id="cactus__search" data-astro-cid-otpdt6jm></div> </div> </div> </dialog> </site-search> <script type="module" src="/_astro/Search.astro_astro_type_script_index_0_lang.Cl-s57jH.js"></script>   <theme-toggle class="ms-2 sm:ms-4"> <button class="relative h-9 w-9 rounded-md p-2 hover:text-accent" type="button"> <span class="sr-only">Dark Theme</span> <svg aria-hidden="true" class="absolute start-1/2 top-1/2 h-7 w-7 -translate-x-1/2 -translate-y-1/2 scale-100 opacity-100 transition-all dark:scale-0 dark:opacity-0" fill="none" focusable="false" id="sun-svg" stroke-width="1.5" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"> <path d="M12 18C15.3137 18 18 15.3137 18 12C18 8.68629 15.3137 6 12 6C8.68629 6 6 8.68629 6 12C6 15.3137 8.68629 18 12 18Z" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"></path> <path d="M22 12L23 12" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"></path> <path d="M12 2V1" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"></path> <path d="M12 23V22" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"></path> <path d="M20 20L19 19" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"></path> <path d="M20 4L19 5" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"></path> <path d="M4 20L5 19" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"></path> <path d="M4 4L5 5" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"></path> <path d="M1 12L2 12" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"></path> </svg> <svg aria-hidden="true" class="absolute start-1/2 top-1/2 h-7 w-7 -translate-x-1/2 -translate-y-1/2 scale-0 opacity-0 transition-all dark:scale-100 dark:opacity-100" fill="none" focusable="false" id="moon-svg" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"> <path d="M0 0h24v24H0z" fill="none" stroke="none"></path> <path d="M12 3c.132 0 .263 0 .393 0a7.5 7.5 0 0 0 7.92 12.446a9 9 0 1 1 -8.313 -12.454z"></path> <path d="M17 4a2 2 0 0 0 2 2a2 2 0 0 0 -2 2a2 2 0 0 0 -2 -2a2 2 0 0 0 2 -2"></path> <path d="M19 11h2m-1 -1v2"></path> </svg> </button> </theme-toggle> <script type="module" src="/_astro/ThemeToggle.astro_astro_type_script_index_0_lang.CB-gjd7v.js"></script> <mobile-button> <button aria-expanded="false" aria-haspopup="menu" aria-label="Open main menu" class="group relative ms-4 h-7 w-7 sm:invisible sm:hidden" id="toggle-navigation-menu" type="button"> <svg aria-hidden="true" class="absolute start-1/2 top-1/2 h-full w-full -translate-x-1/2 -translate-y-1/2 transition-all group-aria-expanded:scale-0 group-aria-expanded:opacity-0" fill="none" focusable="false" id="line-svg" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"> <path d="M3.75 9h16.5m-16.5 6.75h16.5" stroke-linecap="round" stroke-linejoin="round"></path> </svg> <svg aria-hidden="true" class="absolute start-1/2 top-1/2 h-full w-full -translate-x-1/2 -translate-y-1/2 scale-0 text-accent opacity-0 transition-all group-aria-expanded:scale-100 group-aria-expanded:opacity-100" class="text-accent" fill="none" focusable="false" id="cross-svg" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"> <path d="M6 18L18 6M6 6l12 12" stroke-linecap="round" stroke-linejoin="round"></path> </svg> </button> </mobile-button> </header> <script type="module" src="/_astro/Header.astro_astro_type_script_index_0_lang.DuSsDY4R.js"></script> <main id="main">  <div class="gap-x-10 lg:flex lg:items-start"> <aside class="sticky top-20 order-2 -me-32 hidden basis-64 lg:block"> <!-- 修改：目录 --> <h2 class="title text-lg">导览</h2> <ul class="mt-4 text-xs"> <li class> <a aria-label="Scroll to section: 开源编码器" class="block line-clamp-2 hover:text-accent mt-3" href="#开源编码器"><span class="me-0.5">#</span>开源编码器</a> <ul> <li class="ms-2"> <a aria-label="Scroll to section: 辅助工具" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#辅助工具"><span class="me-0.5">#</span>辅助工具</a>  </li> </ul> </li><li class> <a aria-label="Scroll to section: SvtAv1EncApp 参数" class="block line-clamp-2 hover:text-accent mt-3" href="#svtav1encapp-参数"><span class="me-0.5">#</span>SvtAv1EncApp 参数</a> <ul> <li class="ms-2"> <a aria-label="Scroll to section: —crf ${1-63}" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#crf-1-63"><span class="me-0.5">#</span>—crf ${1-63}</a>  </li><li class="ms-2"> <a aria-label="Scroll to section: —enable-overlays ${0,1}" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#enable-overlays-01"><span class="me-0.5">#</span>—enable-overlays ${0,1}</a>  </li><li class="ms-2"> <a aria-label="Scroll to section: —enable-restoration ${0,1}" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#enable-restoration-01"><span class="me-0.5">#</span>—enable-restoration ${0,1}</a>  </li><li class="ms-2"> <a aria-label="Scroll to section: —enable-tf ${0,1}" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#enable-tf-01"><span class="me-0.5">#</span>—enable-tf ${0,1}</a>  </li><li class="ms-2"> <a aria-label="Scroll to section: —film-grain ${0-50}" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#film-grain-0-50"><span class="me-0.5">#</span>—film-grain ${0-50}</a>  </li><li class="ms-2"> <a aria-label="Scroll to section: —input-depth ${8, 10}" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#input-depth-8-10"><span class="me-0.5">#</span>—input-depth ${8, 10}</a>  </li><li class="ms-2"> <a aria-label="Scroll to section: —keyint ${int}" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#keyint-int"><span class="me-0.5">#</span>—keyint ${int}</a>  </li><li class="ms-2"> <a aria-label="Scroll to section: —preset ${0-13}" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#preset-0-13"><span class="me-0.5">#</span>—preset ${0-13}</a>  </li><li class="ms-2"> <a aria-label="Scroll to section: —scd ${0,1}" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#scd-01"><span class="me-0.5">#</span>—scd ${0,1}</a>  </li> </ul> </li><li class> <a aria-label="Scroll to section: 其他技巧" class="block line-clamp-2 hover:text-accent mt-3" href="#其他技巧"><span class="me-0.5">#</span>其他技巧</a> <ul> <li class="ms-2"> <a aria-label="Scroll to section: 通过FFMPEG管道输入SvtAv1EncApp" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#通过ffmpeg管道输入svtav1encapp"><span class="me-0.5">#</span>通过FFMPEG管道输入SvtAv1EncApp</a>  </li><li class="ms-2"> <a aria-label="Scroll to section: 改变帧率" class="block line-clamp-2 hover:text-accent mt-2 text-[0.6875rem]" href="#改变帧率"><span class="me-0.5">#</span>改变帧率</a>  </li> </ul> </li><li class> <a aria-label="Scroll to section: 当前问题" class="block line-clamp-2 hover:text-accent mt-3" href="#当前问题"><span class="me-0.5">#</span>当前问题</a>  </li><li class> <a aria-label="Scroll to section: 脚注：" class="block line-clamp-2 hover:text-accent mt-3" href="#footnote-label"><span class="me-0.5">#</span>脚注：</a>  </li> </ul> </aside> <article class="flex-grow break-words" data-pagefind-body> <div id="blog-hero"><h1 class="title"> SVT-AV1 编码指南 </h1> <div class="flex flex-wrap items-center gap-x-3 gap-y-2"> <p class="font-semibold"> <time datetime="2024-12-19T00:00:00.000Z" title="2024-12-19T00:00:00.000Z">2024年12月19日</time> /   7 min read </p>  </div> <div class="mt-2"> <svg aria-hidden="true" class="inline-block h-6 w-6" fill="none" focusable="false" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"> <path d="M0 0h24v24H0z" fill="none" stroke="none"></path> <path d="M7.859 6h-2.834a2.025 2.025 0 0 0 -2.025 2.025v2.834c0 .537 .213 1.052 .593 1.432l6.116 6.116a2.025 2.025 0 0 0 2.864 0l2.834 -2.834a2.025 2.025 0 0 0 0 -2.864l-6.117 -6.116a2.025 2.025 0 0 0 -1.431 -.593z"></path> <path d="M17.573 18.407l2.834 -2.834a2.025 2.025 0 0 0 0 -2.864l-7.117 -7.116"></path> <path d="M6 9h-.01"></path> </svg> <span class="contents"> <a aria-label="View more blogs with the tag 编码" class="cactus-link inline-block before:content-['#']" data-pagefind-filter="tag" href="/tags/编码/">编码 </a> </span>  </div></div> <!-- 修改：博文宽度 --> <div class="prose prose-sm prose-cactus mt-12 max-w-2xl prose-headings:font-semibold prose-headings:text-accent-2 prose-headings:before:absolute prose-headings:before:-ms-4 prose-headings:before:text-accent sm:prose-headings:before:content-['#'] sm:prose-th:before:content-none">  <p>源链接：<a href="https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95" rel="nofollow, noreferrer" target="_blank">https://gist.github.com/dvaupel/716598fc9e7c2d436b54ae00f7a34b95</a></p>
<h2 id="开源编码器">开源编码器</h2>
<ul>
<li>AOMEnc<sup><a href="#user-content-fn-4" id="user-content-fnref-4" data-footnote-ref="" aria-describedby="footnote-label">1</a></sup>：由AOM开发，具有最多功能和最高质量的参考编码器。</li>
<li>SVT-AV1：由Intel开发，性能高且针对并行性优化的生产级编码器。</li>
<li>Rav1e：由Mozilla/Xiph开发，被Vimeo使用<sup><a href="#user-content-fn-1" id="user-content-fnref-1" data-footnote-ref="" aria-describedby="footnote-label">2</a></sup>。</li>
</ul>
<p>由于这些工具仍在积极开发中，你应该总是使用最新版本，并在必要时自己编译独立的编码器。</p>
<p>我选择了SVT-AV1，因为它在我的中档CPU上提供了最佳的速度/质量权衡。</p>
<h3 id="辅助工具">辅助工具</h3>
<ul>
<li><a href="https://github.com/HandBrake/HandBrake" rel="nofollow, noreferrer" target="_blank">handbreak</a>：用于FFmpeg管道的图形界面。</li>
<li>Av1an：跨平台的现代编码器包装器，带有方便的额外功能和性能提升，如增强的多线程、停止/继续编码、VMAF质量设置。</li>
<li>StaxRip<sup><a href="#user-content-fn-6" id="user-content-fnref-6" data-footnote-ref="" aria-describedby="footnote-label">3</a></sup>：用于高级编码的Windows图形界面。</li>
<li>NEAV1E<sup><a href="#user-content-fn-3" id="user-content-fnref-3" data-footnote-ref="" aria-describedby="footnote-label">4</a></sup>：Windows图形界面编码器。</li>
<li>nmkoder<sup><a href="#user-content-fn-2" id="user-content-fnref-2" data-footnote-ref="" aria-describedby="footnote-label">5</a></sup>：用于编码和分析的Windows图形界面。</li>
</ul>
<h2 id="svtav1encapp-参数">SvtAv1EncApp 参数</h2>
<p>官方参数文档：</p>
<p><a href="https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/svt-av1_encoder_user_guide.md" rel="nofollow, noreferrer" target="_blank">https://gitlab.com/AOMediaCodec/SVT-AV1/-/blob/master/Docs/svt-av1_encoder_user_guide.md</a></p>
<p>以下经验法则来自于个人测试和外部资源，如<a href="https://www.reddit.com/r/AV1/" rel="nofollow, noreferrer" target="_blank">AV1 subreddit</a>。</p>
<h3 id="crf-1-63">—crf {1-63}</h3>
<p>在默认的速率控制模式 (<code>--rc 0</code>) 下，这是控制输出视觉质量的主要参数。</p>
<p>编码器试图确保恒定的质量，而不考虑结果比特率。较低的CRF意味着更高的质量和更大的文件大小。</p>
<p>规则：<code>CRF 30</code>是一个好的起点，大致相当于 x265 的 <code>CRF 21</code>。</p>
<h3 id="enable-overlays-01">—enable-overlays {0,1}</h3>
<p>启用可以提高关键帧的质量，应该始终保持开启（1）。</p>
<h3 id="enable-restoration-01">—enable-restoration {0,1}</h3>
<p>todo</p>
<h3 id="enable-tf-01">—enable-tf {0,1}</h3>
<p>（禁用/启用）alt ref 帧的时间过滤。</p>
<p>禁用 (<code>--enable-tf 0</code>) 可以稍微保留更多细节，但会增加文件大小。</p>
<h3 id="film-grain-0-50">—film-grain {0-50}</h3>
<p>启用胶片颗粒合成。编码器对源进行降噪（值越高表示降噪越强），并将噪声参数保存在查找表中。解码器可以在回放期间使用该表重新创建颗粒。这大幅减少了颗粒画面所需的比特率。</p>
<p>此参数应匹配源素材的颗粒度。但在实验后，我个人得出结论，它目前最适合轻度到中等颗粒度的素材。需要改进。</p>
<h3 id="input-depth-8-10">—input-depth {8, 10}</h3>
<p>视频处理的位深度。通常应设置为10位以减少带状伪影和其他瑕疵。</p>
<p>请注意，这不会改变位深度，只是告诉编码器源是什么。如果你想从8位源创建10位编码，你必须使用ffmpeg转换源（见下文）。</p>
<p><strong>问题</strong>：在我的测试中，10位导致某些场景出现卡顿。播放性能仍然是一个问题。</p>
<h3 id="keyint-int">—keyint {int}</h3>
<p>指定关键帧之间的最大距离。较小的间隔使寻址更快，但较大的间隔会减少文件大小。</p>
<p>一般规则：keyint = 10 * 帧速率，例如24 fps时为240。</p>
<h3 id="preset-0-13">—preset {0-13}</h3>
<p>编码速度和效率，越高效率越高。</p>
<p>粗略估计：<code>8</code>类似于x265 <code>medium</code>，<code>6</code>类似于x265 <code>slow</code>。</p>
<p>每个步骤的编码时间变化很大。以下是一些快速测试的结果，使用的是30秒、1080p、24 fps的电影片段，启用了颗粒合成（不具代表性）。</p>








































<table><thead><tr><th align="center">预设</th><th align="center">编码时间 / s</th><th align="center">文件大小 / MB</th></tr></thead><tbody><tr><td align="center">3</td><td align="center">781</td><td align="center">10.4</td></tr><tr><td align="center">4</td><td align="center">340</td><td align="center">10.6</td></tr><tr><td align="center">5</td><td align="center">231</td><td align="center">10.6</td></tr><tr><td align="center">6</td><td align="center">146</td><td align="center">10.8</td></tr><tr><td align="center">7</td><td align="center">115</td><td align="center">10.9</td></tr><tr><td align="center">8</td><td align="center">109</td><td align="center">10.8</td></tr></tbody></table>
<p>规则：合理的时间/质量比率在4-8之间，6是一个好的起点。>=8适用于实时编码（直播），&#x3C;4很少值得。</p>
<h3 id="scd-01">—scd {0,1}</h3>
<p>禁用/启用场景变化检测。除非你使用恒定比特率，否则总是有好处的。</p>
<h2 id="其他技巧">其他技巧</h2>
<h3 id="通过ffmpeg管道输入svtav1encapp">通过FFMPEG管道输入SvtAv1EncApp</h3>
<p>SVT编码器只接受未压缩的Y4MPEG流（<code>*.y4m</code>）。如果你的文件是其他格式，你可以使用FFMPEG解码它。</p>
<div class="expressive-code"><link rel="stylesheet" href="/_astro/ec.x0vho.css"><script type="module" src="/_astro/ec.8zarh.js"></script><figure class="frame"><figcaption class="header"></figcaption><pre data-language="plaintext"><code><div class="ec-line"><div class="code"><span style="--0:#f8f8f2;--1:#24292e">ffmpeg -i input.mp4 -pix_fmt yuv420p10le -f yuv4mpegpipe -strict -1  - | SvtAv1EncApp -i stdin ...</span></div></div></code></pre><div class="copy"><button title="Copy to clipboard" data-copied="Copied!" data-code="ffmpeg -i input.mp4 -pix_fmt yuv420p10le -f yuv4mpegpipe -strict -1  - | SvtAv1EncApp -i stdin ..."><div></div></button></div></figure></div>
<h3 id="改变帧率">改变帧率</h3>
<p>SVT编码器本身不能改变帧率。它的 <code>--fps</code> 标志仅作为内部提示用于速率控制（我认为）。</p>
<p>相反，我们可以使用ffmpeg的 <code>fps</code> 滤镜，并将具有所需帧率的流通过管道输入编码器。</p>
<div class="expressive-code"><figure class="frame"><figcaption class="header"></figcaption><pre data-language="plaintext"><code><div class="ec-line"><div class="code"><span style="--0:#f8f8f2;--1:#24292e">ffmpeg -i in.mp4 -vf fps=fps=30 -strict -1 -f yuv4mpegpipe - |</span></div></div><div class="ec-line"><div class="code"><span style="--0:#f8f8f2;--1:#24292e">SvtAv1EncApp -i stdin --fps 30 --keyint 300 &#x3C;其他选项...> -b out.ivf</span></div></div></code></pre><div class="copy"><button title="Copy to clipboard" data-copied="Copied!" data-code="ffmpeg -i in.mp4 -vf fps=fps=30 -strict -1 -f yuv4mpegpipe - |SvtAv1EncApp -i stdin --fps 30 --keyint 300 <其他选项...> -b out.ivf"><div></div></button></div></figure></div>
<h2 id="当前问题">当前问题</h2>
<ul>
<li>在普通消费硬件上，10位播放性能还不够可靠。</li>
<li>即使在高比特率下，AV1也会明显平滑视频。这在雨、雪等场景中尤为明显，很难保持细节。</li>
<li>粒子电影是AV1的一个弱点。即使启用了电影颗粒合成，也很难获得满意的结果。以下是目前我们能做的最好的：
<div class="expressive-code"><figure class="frame"><figcaption class="header"></figcaption><pre data-language="plaintext"><code><div class="ec-line"><div class="code"><span style="--0:#f8f8f2;--1:#24292e">SvtAv1EncApp --rc 0 --crf 20 --preset 3 --irefresh-type 1 --keyint 240 --input-depth 10 --enable-overlays 1</span></div></div><div class="ec-line"><div class="code"><span style="--0:#f8f8f2;--1:#24292e">--enable-tf 0 --enable-restoration 0 --film-grain &#x3C;整数></span></div></div></code></pre><div class="copy"><button title="Copy to clipboard" data-copied="Copied!" data-code="SvtAv1EncApp --rc 0 --crf 20 --preset 3 --irefresh-type 1 --keyint 240 --input-depth 10 --enable-overlays 1--enable-tf 0 --enable-restoration 0 --film-grain <整数>"><div></div></button></div></figure></div>
</li>
</ul>
<p>目前（2022年初，SVT-AV1 v0.9.0），它是一个相当有前景但并不完美的编解码器。对于大多数常规内容，它非常好，在小文件大小的情况下实现了高质量，并显然兑现了其比HEVC更高效的承诺。</p>
<p>唯一仍需改进的领域是颗粒感强、细节丰富的电影场景。面对如此高比特率、蓝光质量的源素材，很难实现视觉透明度。如果颗粒合成已经足够好，并且大多数设备都可以顺利解码，那么它可以普遍推荐。目前，它仍然处于晚期实验阶段。</p>
<section data-footnotes="" class="footnotes"><h2 class="" id="footnote-label">脚注：</h2>
<ol>
<li id="user-content-fn-4">
<p><a href="https://aomedia.googlesource.com/aom/" rel="nofollow, noreferrer" target="_blank">https://aomedia.googlesource.com/aom/</a> <a href="#user-content-fnref-4" data-footnote-backref="" aria-label="Back to reference 1" class="data-footnote-backref">↩</a></p>
</li>
<li id="user-content-fn-1">
<p><a href="https://investors.vimeo.com/news-releases/news-release-details/vimeo-introduces-support-royalty-free-video-codec-av1" rel="nofollow, noreferrer" target="_blank">https://investors.vimeo.com/news-releases/news-release-details/vimeo-introduces-support-royalty-free-video-codec-av1</a> <a href="#user-content-fnref-1" data-footnote-backref="" aria-label="Back to reference 2" class="data-footnote-backref">↩</a></p>
</li>
<li id="user-content-fn-6">
<p><a href="https://github.com/staxrip/staxrip" rel="nofollow, noreferrer" target="_blank">https://github.com/staxrip/staxrip</a> <a href="#user-content-fnref-6" data-footnote-backref="" aria-label="Back to reference 3" class="data-footnote-backref">↩</a></p>
</li>
<li id="user-content-fn-3">
<p><a href="https://github.com/Alkl58/NotEnoughAV1Encodes" rel="nofollow, noreferrer" target="_blank">https://github.com/Alkl58/NotEnoughAV1Encodes</a> <a href="#user-content-fnref-3" data-footnote-backref="" aria-label="Back to reference 4" class="data-footnote-backref">↩</a></p>
</li>
<li id="user-content-fn-2">
<p><a href="https://github.com/n00mkrad/nmkoder" rel="nofollow, noreferrer" target="_blank">https://github.com/n00mkrad/nmkoder</a> <a href="#user-content-fnref-2" data-footnote-backref="" aria-label="Back to reference 5" class="data-footnote-backref">↩</a></p>
</li>
</ol>
</section>   </div> </article> </div> <button aria-label="Back to Top" class="z-90 fixed bottom-8 end-4 flex h-10 w-10 translate-y-28 items-center justify-center rounded-full border-2 border-transparent bg-zinc-200 text-3xl opacity-0 transition-all duration-300 hover:border-link data-[show=true]:translate-y-0 data-[show=true]:opacity-100 dark:bg-zinc-700 sm:end-8 sm:h-12 sm:w-12" data-show="false" id="to-top-btn"><svg aria-hidden="true" class="h-6 w-6" fill="none" focusable="false" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"> <path d="M4.5 15.75l7.5-7.5 7.5 7.5" stroke-linecap="round" stroke-linejoin="round"></path> </svg> </button>  </main> <footer class="mt-auto flex w-full flex-col items-center justify-center gap-y-2 pb-4 pt-20 text-center align-top font-semibold text-gray-600 dark:text-gray-400 sm:flex-row sm:justify-between sm:text-xs"> <div class="me-0 sm:me-4">
&copy; 三七 <!-- 修改 --> <!-- {year}.<span class="inline-block">&nbsp;🚀&nbsp;Astro Cactus</span> --> 2026 </div> <nav aria-label="More on this site" class="flex gap-x-2 sm:gap-x-0 sm:divide-x sm:divide-gray-500"> <a class="px-4 py-2 sm:py-0 sm:hover:text-textColor sm:hover:underline" href="/"> 主页 </a><a class="px-4 py-2 sm:py-0 sm:hover:text-textColor sm:hover:underline" href="/about/"> 关于 </a><a class="px-4 py-2 sm:py-0 sm:hover:text-textColor sm:hover:underline" href="/posts/"> 随笔 </a><a class="px-4 py-2 sm:py-0 sm:hover:text-textColor sm:hover:underline" href="/notes/"> 札记 </a> </nav> </footer> </body></html> <script type="module">const e=document.getElementById("to-top-btn"),n=document.getElementById("blog-hero");function c(t){t.forEach(o=>{e.dataset.show=(!o.isIntersecting).toString()})}e.addEventListener("click",()=>{document.documentElement.scrollTo({behavior:"smooth",top:0})});const r=new IntersectionObserver(c);r.observe(n);</script>
