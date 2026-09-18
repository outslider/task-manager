-- タスク管理システム スキーマ定義（参照用）
-- app/db.py が起動時に自動生成・移行するため、通常このファイルを流す必要はありません。
-- 生成: mysqldump --no-data --skip-comments --compact

/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `attachments` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `task_id` int(11) DEFAULT NULL,
  `issue_id` int(11) DEFAULT NULL,
  `kind` varchar(10) NOT NULL,
  `name` varchar(300) NOT NULL,
  `url` varchar(2000) NOT NULL DEFAULT '',
  `stored_name` varchar(200) NOT NULL DEFAULT '',
  `size` bigint(20) NOT NULL DEFAULT 0,
  `mime` varchar(150) NOT NULL DEFAULT '',
  `uploaded_by` int(11) DEFAULT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_attachments_task` (`task_id`),
  KEY `idx_attachments_issue` (`issue_id`),
  KEY `fk_att_user` (`uploaded_by`),
  CONSTRAINT `fk_att_issue` FOREIGN KEY (`issue_id`) REFERENCES `issues` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_att_task` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_att_user` FOREIGN KEY (`uploaded_by`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `checkins` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` int(11) NOT NULL,
  `checkin_date` date NOT NULL,
  `note` text DEFAULT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_checkin` (`user_id`,`checkin_date`),
  CONSTRAINT `fk_checkin_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `comments` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `task_id` int(11) DEFAULT NULL,
  `issue_id` int(11) DEFAULT NULL,
  `user_id` int(11) DEFAULT NULL,
  `body` text NOT NULL,
  `kind` varchar(20) NOT NULL DEFAULT 'comment',
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_comments_task` (`task_id`),
  KEY `idx_comments_issue` (`issue_id`),
  KEY `fk_comment_user` (`user_id`),
  CONSTRAINT `fk_comment_issue` FOREIGN KEY (`issue_id`) REFERENCES `issues` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_comment_task` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_comment_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `company_holidays` (
  `day` date NOT NULL,
  `name` varchar(100) NOT NULL DEFAULT '休業日',
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`day`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `group_members` (
  `group_id` int(11) NOT NULL,
  `user_id` int(11) NOT NULL,
  PRIMARY KEY (`group_id`,`user_id`),
  KEY `fk_gm_user` (`user_id`),
  CONSTRAINT `fk_gm_group` FOREIGN KEY (`group_id`) REFERENCES `user_groups` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_gm_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `issue_tasks` (
  `issue_id` int(11) NOT NULL,
  `task_id` int(11) NOT NULL,
  PRIMARY KEY (`issue_id`,`task_id`),
  KEY `idx_issue_tasks_task` (`task_id`),
  CONSTRAINT `fk_it_issue` FOREIGN KEY (`issue_id`) REFERENCES `issues` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_it_task` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `issues` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `project_id` int(11) NOT NULL,
  `seq` int(11) NOT NULL,
  `title` varchar(300) NOT NULL,
  `description` text DEFAULT NULL,
  `category` varchar(20) NOT NULL DEFAULT '',
  `status` varchar(20) NOT NULL DEFAULT 'open',
  `severity` tinyint(4) NOT NULL DEFAULT 1,
  `owner_id` int(11) DEFAULT NULL,
  `raised_by` int(11) DEFAULT NULL,
  `raised_on` date NOT NULL,
  `due_date` date DEFAULT NULL,
  `resolved_on` date DEFAULT NULL,
  `resolution` text DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_issue_seq` (`project_id`,`seq`),
  KEY `idx_issues_status` (`project_id`,`status`),
  KEY `idx_issues_owner` (`owner_id`),
  KEY `idx_issues_due` (`due_date`),
  KEY `fk_issue_raiser` (`raised_by`),
  CONSTRAINT `fk_issue_owner` FOREIGN KEY (`owner_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_issue_project` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_issue_raiser` FOREIGN KEY (`raised_by`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `notification_mutes` (
  `user_id` int(11) NOT NULL,
  `project_id` int(11) NOT NULL,
  PRIMARY KEY (`user_id`,`project_id`),
  KEY `fk_mute_project` (`project_id`),
  CONSTRAINT `fk_mute_project` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_mute_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `notifications` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` int(11) NOT NULL,
  `task_id` int(11) DEFAULT NULL,
  `type` varchar(30) NOT NULL,
  `title` varchar(300) NOT NULL,
  `body` text DEFAULT NULL,
  `is_read` tinyint(1) NOT NULL DEFAULT 0,
  `dedupe_key` varchar(190) DEFAULT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_notif_dedupe` (`user_id`,`dedupe_key`),
  KEY `idx_notif_user` (`user_id`,`is_read`),
  KEY `fk_notif_task` (`task_id`),
  CONSTRAINT `fk_notif_task` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_notif_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `project_members` (
  `project_id` int(11) NOT NULL,
  `principal_type` varchar(10) NOT NULL,
  `principal_id` int(11) NOT NULL,
  `role` varchar(20) NOT NULL DEFAULT 'editor',
  PRIMARY KEY (`project_id`,`principal_type`,`principal_id`),
  CONSTRAINT `fk_pm_project` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `projects` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(200) NOT NULL,
  `description` text DEFAULT NULL,
  `color` varchar(20) NOT NULL DEFAULT '#4f8cff',
  `owner_id` int(11) DEFAULT NULL,
  `archived` tinyint(1) NOT NULL DEFAULT 0,
  `slack_webhook_url` varchar(300) NOT NULL DEFAULT '',
  `notify_enabled` tinyint(1) NOT NULL DEFAULT 1,
  `slack_events` varchar(120) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_proj_owner` (`owner_id`),
  CONSTRAINT `fk_proj_owner` FOREIGN KEY (`owner_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `recurrences` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `project_id` int(11) NOT NULL,
  `title` varchar(300) NOT NULL,
  `description` text DEFAULT NULL,
  `category` varchar(20) NOT NULL DEFAULT '',
  `priority` tinyint(4) NOT NULL DEFAULT 1,
  `assignee_id` int(11) DEFAULT NULL,
  `estimate_hours` decimal(6,1) DEFAULT NULL,
  `parent_id` int(11) DEFAULT NULL,
  `freq` varchar(10) NOT NULL,
  `interval_n` int(11) NOT NULL DEFAULT 1,
  `weekdays` varchar(20) NOT NULL DEFAULT '',
  `month_day` tinyint(4) DEFAULT NULL,
  `lead_days` int(11) NOT NULL DEFAULT 3,
  `next_on` date NOT NULL,
  `last_created_on` date DEFAULT NULL,
  `active` tinyint(1) NOT NULL DEFAULT 1,
  `created_by` int(11) DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_recurrence_project` (`project_id`),
  KEY `idx_recurrence_next` (`active`,`next_on`),
  KEY `fk_rec_assignee` (`assignee_id`),
  KEY `fk_rec_parent` (`parent_id`),
  KEY `fk_rec_creator` (`created_by`),
  CONSTRAINT `fk_rec_assignee` FOREIGN KEY (`assignee_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_rec_creator` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_rec_parent` FOREIGN KEY (`parent_id`) REFERENCES `tasks` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_rec_project` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `sessions` (
  `token` varchar(64) NOT NULL,
  `user_id` int(11) NOT NULL,
  `created_at` datetime NOT NULL,
  `expires_at` datetime NOT NULL,
  PRIMARY KEY (`token`),
  KEY `idx_sessions_user` (`user_id`),
  CONSTRAINT `fk_session_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `settings` (
  `setting_key` varchar(100) NOT NULL,
  `setting_value` text NOT NULL,
  PRIMARY KEY (`setting_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `shared_links` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `project_id` int(11) DEFAULT NULL,
  `title` varchar(200) NOT NULL,
  `url` varchar(2000) NOT NULL,
  `note` varchar(500) NOT NULL DEFAULT '',
  `sort_order` int(11) NOT NULL DEFAULT 0,
  `created_by` int(11) DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_links_project` (`project_id`,`sort_order`),
  KEY `fk_link_user` (`created_by`),
  CONSTRAINT `fk_link_project` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_link_user` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `task_categories` (
  `value` varchar(30) NOT NULL,
  `label` varchar(60) NOT NULL,
  `color` varchar(20) NOT NULL,
  `icon` varchar(8) NOT NULL DEFAULT '',
  `sort_order` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`value`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `task_deps` (
  `task_id` int(11) NOT NULL,
  `depends_on_id` int(11) NOT NULL,
  PRIMARY KEY (`task_id`,`depends_on_id`),
  KEY `idx_dep_on` (`depends_on_id`),
  CONSTRAINT `fk_dep_on` FOREIGN KEY (`depends_on_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_dep_task` FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `task_statuses` (
  `status_key` varchar(20) NOT NULL,
  `label` varchar(40) NOT NULL,
  `color` varchar(20) NOT NULL,
  `sort_order` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`status_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `tasks` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `project_id` int(11) NOT NULL,
  `parent_id` int(11) DEFAULT NULL,
  `title` varchar(300) NOT NULL,
  `description` text DEFAULT NULL,
  `category` varchar(20) NOT NULL DEFAULT '',
  `status` varchar(20) NOT NULL DEFAULT 'todo',
  `priority` tinyint(4) NOT NULL DEFAULT 1,
  `assignee_id` int(11) DEFAULT NULL,
  `start_date` date DEFAULT NULL,
  `due_date` date DEFAULT NULL,
  `progress` int(11) NOT NULL DEFAULT 0,
  `estimate_hours` decimal(6,1) DEFAULT NULL,
  `actual_hours` decimal(6,1) NOT NULL DEFAULT 0.0,
  `is_milestone` tinyint(1) NOT NULL DEFAULT 0,
  `marker` varchar(10) NOT NULL DEFAULT '',
  `sort_order` int(11) NOT NULL DEFAULT 0,
  `created_by` int(11) DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  `completed_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_tasks_project` (`project_id`),
  KEY `idx_tasks_parent` (`parent_id`),
  KEY `idx_tasks_assignee` (`assignee_id`),
  KEY `idx_tasks_due` (`due_date`),
  KEY `idx_tasks_category` (`category`),
  KEY `fk_task_creator` (`created_by`),
  CONSTRAINT `fk_task_assignee` FOREIGN KEY (`assignee_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_task_creator` FOREIGN KEY (`created_by`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_task_parent` FOREIGN KEY (`parent_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_task_project` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `todos` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` int(11) NOT NULL,
  `title` varchar(300) NOT NULL,
  `note` varchar(1000) NOT NULL DEFAULT '',
  `due_date` date DEFAULT NULL,
  `is_done` tinyint(1) NOT NULL DEFAULT 0,
  `sort_order` int(11) NOT NULL DEFAULT 0,
  `done_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_todos_user` (`user_id`,`is_done`,`sort_order`),
  CONSTRAINT `fk_todo_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `user_groups` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(150) NOT NULL,
  `description` varchar(500) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `email` varchar(190) NOT NULL,
  `name` varchar(120) NOT NULL,
  `password_hash` varchar(255) NOT NULL,
  `role` varchar(20) NOT NULL DEFAULT 'member',
  `is_active` tinyint(1) NOT NULL DEFAULT 1,
  `email_notify` tinyint(1) NOT NULL DEFAULT 1,
  `notify_assigned` tinyint(1) NOT NULL DEFAULT 1,
  `notify_comment` tinyint(1) NOT NULL DEFAULT 1,
  `notify_due` tinyint(1) NOT NULL DEFAULT 1,
  `notify_digest` tinyint(1) NOT NULL DEFAULT 1,
  `notify_mention` tinyint(1) NOT NULL DEFAULT 1,
  `avatar_color` varchar(20) NOT NULL DEFAULT '#4f8cff',
  `ui_theme` varchar(10) NOT NULL DEFAULT 'auto',
  `ui_accent` varchar(20) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `email` (`email`)
) ENGINE=InnoDB AUTO_INCREMENT=7 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
