namespace App.Services {
    public interface IRepo { }
    public record User(string Name);
    public class UserService : IRepo {
        public string Name { get; set; }
        public User Get(int id) { return Find(id); }
        private User Find(int id) { return null; }
    }
}
