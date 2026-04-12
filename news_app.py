"""Legacy news + download mixed app entry.

This file exists to keep the old functionality accessible
after main.py switched to downloader-only multi-tenant mode.
"""


if __name__ == "__main__":
    from legacy_all_in_one_app import Settings
    from legacy_all_in_one_app import app
    from legacy_all_in_one_app import logger

    logger.info("=" * 50)
    logger.info("Legacy App: Downloader + News Robot")
    logger.info("访问地址: http://localhost:%s", Settings.PORT)
    logger.info("=" * 50)
    app.run(host=Settings.HOST, port=Settings.PORT, debug=Settings.DEBUG, threaded=True, use_reloader=False)
