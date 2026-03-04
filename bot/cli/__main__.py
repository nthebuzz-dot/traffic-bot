import argparse
import sys
from bot.config_handler import ConfigHandler
from bot.traffic_bot import TrafficBot
from bot.logger import setup_logger

logger = setup_logger(__name__)


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Web Traffic Bot - Load testing and traffic simulation tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Launch the web dashboard (recommended)
  web-traffic-bot --dashboard

  # Run directly from the command line
  web-traffic-bot --url https://yoursite.com --sessions 20 --duration 600
  web-traffic-bot --config config/config.yaml
  web-traffic-bot --config config/config.yaml --sessions 50 --no-headless
        """
    )

    # ------------------------------------------------------------------ #
    # Dashboard mode                                                        #
    # ------------------------------------------------------------------ #
    parser.add_argument(
        '--dashboard', '-d',
        action='store_true',
        help='Launch the web dashboard (default: http://localhost:5000)'
    )
    parser.add_argument(
        '--host',
        default='0.0.0.0',
        help='Dashboard host (default: 0.0.0.0)'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=5000,
        help='Dashboard port (default: 5000)'
    )

    # ------------------------------------------------------------------ #
    # CLI bot mode                                                          #
    # ------------------------------------------------------------------ #
    parser.add_argument(
        '--url', '--target-url',
        dest='url',
        help='Target URL to test'
    )
    parser.add_argument(
        '--config', '-c',
        help='Path to YAML config file'
    )
    parser.add_argument(
        '--sessions',
        type=int,
        help='Number of sessions to run'
    )
    parser.add_argument(
        '--duration',
        type=int,
        help='Total duration in seconds'
    )
    parser.add_argument(
        '--session-duration',
        type=int,
        dest='session_duration',
        help='Duration per session in seconds'
    )
    parser.add_argument(
        '--headless',
        action='store_true',
        default=True,
        help='Run in headless mode (default)'
    )
    parser.add_argument(
        '--no-headless',
        action='store_false',
        dest='headless',
        help='Run with a visible browser window'
    )
    parser.add_argument(
        '--proxy',
        action='append',
        dest='proxies',
        metavar='PROXY_URL',
        help='Proxy URL (repeat for multiple proxies)'
    )

    args = parser.parse_args()

    # ------------------------------------------------------------------ #
    # Dashboard branch                                                      #
    # ------------------------------------------------------------------ #
    if args.dashboard:
        try:
            from bot.dashboard.app import run_dashboard
        except ImportError:
            logger.error(
                "Flask is required for the dashboard. "
                "Install it with:  pip install flask"
            )
            sys.exit(1)
        run_dashboard(host=args.host, port=args.port)
        return

    # ------------------------------------------------------------------ #
    # CLI bot branch                                                        #
    # ------------------------------------------------------------------ #
    try:
        config = ConfigHandler(args.config)

        if args.url:
            config.target_url = args.url
        if args.sessions is not None:
            config.sessions_count = args.sessions
        if args.duration is not None:
            config.duration_seconds = args.duration
        if args.session_duration is not None:
            config.session_duration = args.session_duration
        if args.headless is not None:
            config.headless = args.headless
        if args.proxies:
            config.proxies = args.proxies

        config.validate()

        bot = TrafficBot(config)
        bot.run()

    except (ValueError, FileNotFoundError) as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
