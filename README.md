# What is SponsoredIssues?

SponsoredIssues is a self-hosted website that enables you, the noble FOSS developer, to receive donations for your GitHub issues. At the same time, SponsoredIssues allows your users to make their voices heard, by donating to the GitHub issues that matter most to them.

![SponsoredIssues website screenshot](/home/benv/git/sponsoredissues/static/images/issues-page-mockup.png)

# Incentives for Donors and Developers

The SponsoredIssues issue listing is ranked in descending order of donation total. This is helpful because:

1. It allows you (the FOSS developer) to easily see which GitHub issues are most important to your users.
2. It gives your users an incentive to donate to your project, in order to upvote the issues that they care about.

The SponsoredIssues issue ranking is an informal social contract between you (the FOSS developer) and your users. There is no strict requirement that you resolve your GitHub issues in the same order as the SponsoredIssues ranking, but it is in your best interest to do so. If you consistently honor the SponsoredIssues ranking, your users will see that their donations actually matter, and they will be more likely to donate in the future.

SponsoredIssues does not automatically make all GitHub issues for your repo fundable. Instead, you (the FOSS developer) must manually add the specific GitHub issues that you want to appear on your SponsoredIssues page. This explicit approval step is important because new GitHub issues can be created by *anyone*, and in practice many issues are:

1. Trivial (e.g. a basic question about how to use the software).

2. Not clearly communicated (e.g. a vague feature request).

3. Not technically feasible (e.g. an impossible-to-implement feature).

4. Not aligned with the goals of your project.

Since you (the FOSS developer) choose which GitHub issues are fundable, your users cannot steer the development work in a direction that doesn't agree with your own vision for the project (scope creep, feature bloat, etc.)

# Self-hosting Setup

1. Create a Linux VPS (virtual private server) instance with any cloud provider (e.g. a DigitalOcean Droplet).

2. Configure SSH keys on your VPS and disable password access.

3. Install `podman` on your VPS.

   ```bash
   # Example commands for Ubuntu.
   sudo apt update
   sudo apt install podman
   ```

# Development Setup

## Run the Docker Image Locally

```bash
# Build the Docker image.
# Note: You can replace `sponsoredissues-image` with any name you like.
podman build -t sponsoredissues-image .

# Run the Docker image, and serve the website at https://localhost:8000.
podman run -p 8000:8000 sponsoredissues-image
```

## Deploy the Docker Image to your VPS

Create a connection to your VPS in `podman` (one-time setup):

```bash
# One time setup.
# Note: You can replace `sponsoredissues-server` with any name you like.
podman system connection add sponsoredissues-server ssh://<user>@<remote_host_ip_or_hostname>
```

Build the image and copy it to your VPS:

```bash
# Build the Docker image.
podman build -t sponsoredissues-image .

# Copy the Docker image to your VPS.
podman image scp sponsoredissues-image sponsoredissues-server::sponsoredissues-image
```