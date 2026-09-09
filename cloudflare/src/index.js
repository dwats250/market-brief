const WORKFLOW = "schedule.yml";

export default {
  async scheduled(controller, env) {
    const repository = env.GITHUB_REPOSITORY;
    const token = env.GH_DISPATCH_TOKEN;
    if (!repository || !token) {
      throw new Error("Cloudflare scheduler is missing its GitHub dispatch configuration");
    }

    const response = await fetch(
      `https://api.github.com/repos/${repository}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "User-Agent": "market-brief-cloudflare-scheduler",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({
          ref: "main",
          inputs: { cloudflare_wakeup: "true" },
        }),
      },
    );

    if (!response.ok) {
      const body = (await response.text()).slice(0, 1000);
      console.error(JSON.stringify({
        error: "github_workflow_dispatch_rejected",
        status: response.status,
        body,
      }));
      throw new Error(`GitHub workflow dispatch failed with HTTP ${response.status}`);
    }
  },

  async fetch() {
    return new Response("Market Brief scheduler", { status: 200 });
  },
};
