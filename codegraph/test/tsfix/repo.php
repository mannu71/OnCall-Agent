<?php
class Repo {
  function findUser($id){ return query($id); }
}
function query($id){ return $id; }
