#!/usr/bin/env python3
"""
Seed Firestore with example feature flags and announcements documents.

Usage:
    # Set your GCP project
    export GOOGLE_CLOUD_PROJECT=your-project-id

    # Optional: Use service account
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json

    # Run the script
    python seed_firestore_examples.py

    # Dry run (print documents without writing)
    python seed_firestore_examples.py --dry-run
"""

import argparse
import json
from google.cloud import firestore


# =============================================================================
# FEATURE FLAGS COLLECTION
# =============================================================================

FEATURE_FLAGS_GLOBAL = {
    "_schema_version": "2.0",
    "_updated_at": "2025-01-26T10:00:00Z",

    # Legacy format - simple list for backward compatibility
    "enabled_features": ["dark_mode", "base_capture"],

    # Enhanced format - full definitions with metadata
    "feature_definitions": {
        "dark_mode": {
            "description": "Enable dark mode theme toggle",
            "category": "ui",
            "created_at": "2025-01-15T00:00:00Z",
            "owner": "ui-team",
            "dependencies": [],
            "rollout": {
                "type": "full",
                "percentage": 100
            }
        },
        "new_dashboard": {
            "description": "Redesigned dashboard with metrics",
            "category": "ui",
            "created_at": "2025-01-20T00:00:00Z",
            "owner": "ui-team",
            "dependencies": [],
            "rollout": {
                "type": "percentage",
                "percentage": 25,
                "hash_key": "instance_id"
            }
        },
        "advanced_capture": {
            "description": "Enhanced packet capture with filtering",
            "category": "network",
            "created_at": "2025-01-22T00:00:00Z",
            "owner": "network-team",
            "dependencies": ["base_capture"],
            "rollout": {
                "type": "full",
                "percentage": 100
            }
        },
        "base_capture": {
            "description": "Basic packet capture functionality",
            "category": "network",
            "created_at": "2025-01-10T00:00:00Z",
            "owner": "network-team",
            "dependencies": [],
            "rollout": {
                "type": "full",
                "percentage": 100
            }
        },
        "cvp_integration": {
            "description": "CloudVision Portal integration features",
            "category": "integration",
            "created_at": "2025-01-05T00:00:00Z",
            "owner": "platform-team",
            "dependencies": [],
            "rollout": {
                "type": "full",
                "percentage": 100
            }
        }
    }
}

FEATURE_FLAGS_TOPOLOGIES = {
    "_schema_version": "2.0",
    "_updated_at": "2025-01-26T10:00:00Z",

    "training-level7-cl": {
        "enabled_features": ["advanced_capture", "cvp_integration"],
        "overrides": {
            "new_dashboard": {
                "rollout": {
                    "type": "full",
                    "percentage": 100
                }
            }
        }
    },

    "training-level5-cl": {
        "enabled_features": ["base_capture"],
        "overrides": {}
    },

    "training-level3-cl": {
        "enabled_features": [],
        "overrides": {}
    }
}


# =============================================================================
# ANNOUNCEMENTS COLLECTION
# =============================================================================

ANNOUNCEMENTS_GLOBAL = {
    "_schema_version": "2.0",
    "_updated_at": "2025-01-26T10:00:00Z",

    "announcements": [
        {
            "id": "maint-2025-01-30",
            "title": "Scheduled Maintenance",
            "message": "Platform maintenance scheduled for January 30th, 2025 from 2:00 AM - 4:00 AM UTC.",
            "type": "warning",
            "priority": 90,
            "dismissible": True,
            "start_date": "2025-01-25T00:00:00Z",
            "end_date": "2025-01-30T04:00:00Z",
            "tags": ["maintenance", "scheduled"],
            "links": [
                {
                    "label": "Status Page",
                    "url": "https://status.arista.com",
                    "type": "external"
                }
            ]
        },
        {
            "id": "new-feature-dashboard",
            "title": "New Dashboard Available",
            "message": "Check out our redesigned dashboard with enhanced metrics and visualizations!",
            "type": "info",
            "priority": 50,
            "dismissible": True,
            "start_date": "2025-01-20T00:00:00Z",
            "end_date": "2025-02-20T00:00:00Z",
            "tags": ["feature", "ui", "new"],
            "links": [
                {
                    "label": "Learn More",
                    "url": "/docs/dashboard",
                    "type": "internal"
                },
                {
                    "label": "Try It Now",
                    "url": "/dashboard?new=true",
                    "type": "action"
                }
            ]
        },
        {
            "id": "cert-reminder-q1",
            "title": "Certification Deadline Approaching",
            "message": "Complete your ACE-L4 certification before March 31st to maintain your status.",
            "type": "alert",
            "priority": 80,
            "dismissible": False,
            "start_date": "2025-02-01T00:00:00Z",
            "end_date": "2025-03-31T23:59:59Z",
            "tags": ["certification", "deadline", "ace"],
            "links": [
                {
                    "label": "View Requirements",
                    "url": "https://www.arista.com/certification",
                    "type": "external"
                },
                {
                    "label": "Start Exam",
                    "url": "/exam/start",
                    "type": "action"
                }
            ]
        },
        {
            "id": "welcome-message",
            "title": "Welcome to Arista Training Labs",
            "message": "Get started with your training environment. Check out the lab guide for instructions.",
            "type": "success",
            "priority": 30,
            "dismissible": True,
            "start_date": "2020-01-01T00:00:00Z",
            "end_date": "2030-12-31T23:59:59Z",
            "tags": ["welcome", "getting-started"],
            "links": [
                {
                    "label": "Lab Guide",
                    "url": "/labguides",
                    "type": "internal"
                }
            ]
        }
    ]
}

ANNOUNCEMENTS_TOPOLOGIES = {
    "_schema_version": "2.0",
    "_updated_at": "2025-01-26T10:00:00Z",

    "training-level7-cl": [
        {
            "id": "level7-cvp-update",
            "title": "CVP Version Updated",
            "message": "This topology now uses CloudVision Portal 2024.2.0 with new telemetry features.",
            "type": "success",
            "priority": 60,
            "dismissible": True,
            "start_date": "2025-01-20T00:00:00Z",
            "end_date": "2025-02-28T00:00:00Z",
            "tags": ["cvp", "update", "level7"],
            "links": [
                {
                    "label": "Release Notes",
                    "url": "https://www.arista.com/cvp/release-notes/2024.2.0",
                    "type": "external"
                }
            ]
        }
    ],

    "exam-level5": [
        {
            "id": "exam-instructions",
            "title": "Exam Environment",
            "message": "This is a timed exam environment. You have 90 minutes to complete all tasks.",
            "type": "alert",
            "priority": 100,
            "dismissible": False,
            "start_date": "2020-01-01T00:00:00Z",
            "end_date": "2030-12-31T23:59:59Z",
            "tags": ["exam", "instructions", "important"],
            "links": [
                {
                    "label": "Exam Rules",
                    "url": "/exam/rules",
                    "type": "internal"
                }
            ]
        }
    ],

    "training-level5-cl": [
        {
            "id": "level5-tip",
            "title": "Pro Tip: Use CLI Shortcuts",
            "message": "Speed up your workflow with EOS CLI shortcuts. Try 'sh ip int br' instead of 'show ip interface brief'.",
            "type": "info",
            "priority": 20,
            "dismissible": True,
            "start_date": "2020-01-01T00:00:00Z",
            "end_date": "2030-12-31T23:59:59Z",
            "tags": ["tip", "cli", "level5"],
            "links": []
        }
    ]
}


def seed_firestore(dry_run: bool = False):
    """Seed Firestore with example documents."""

    documents = [
        ("feature-flags", "global", FEATURE_FLAGS_GLOBAL),
        ("feature-flags", "topologies", FEATURE_FLAGS_TOPOLOGIES),
        ("announcements", "global", ANNOUNCEMENTS_GLOBAL),
        ("announcements", "topologies", ANNOUNCEMENTS_TOPOLOGIES),
    ]

    if dry_run:
        print("=" * 60)
        print("DRY RUN - Documents that would be written:")
        print("=" * 60)
        for collection, doc_id, data in documents:
            print(f"\n📁 {collection}/{doc_id}")
            print("-" * 40)
            print(json.dumps(data, indent=2, default=str))
        print("\n" + "=" * 60)
        print("Run without --dry-run to write to Firestore")
        return

    db = firestore.Client()

    for collection, doc_id, data in documents:
        doc_ref = db.collection(collection).document(doc_id)
        doc_ref.set(data)
        print(f"✅ Written: {collection}/{doc_id}")

    print("\n🎉 Firestore seeded successfully!")


def main():
    parser = argparse.ArgumentParser(
        description="Seed Firestore with example feature flags and announcements"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print documents without writing to Firestore"
    )
    args = parser.parse_args()

    seed_firestore(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
